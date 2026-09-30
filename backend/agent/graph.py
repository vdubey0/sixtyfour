"""Bounded LangGraph loop. Counters survive clarification; terminal runs cannot resume."""
import asyncio
import json
import threading
import time
from dataclasses import dataclass, field
from typing import TypedDict
import pandas as pd
from langgraph.graph import StateGraph, START, END
from langgraph.errors import GraphRecursionError
from backend.agent.limits import Limits
from backend.agent.model import Planner, ModelError
from backend.agent.jev import JevRouter, candidates, minimum_probability, MIN_MARGIN, TIMEOUT, DEFER
from backend.agent.prompts import system_prompt
from backend.agent.schemas import Decision
from backend.agent.tools import (CATALOG_FOR_AGENT, METADATA_TOOLS, prepare, execute_prepared,
                                 fingerprint, digest, observation, preview, check_contract, validate_contract)
from backend.blocks.read_csv import run_read
from backend.blocks.save_csv import run_save
from backend.blocks.sixtyfour import APIError, clean_value, setting
from backend.engine.compatibility import DatasetSchema
from backend.engine.execution import RunContext, Cancelled


class GraphState(TypedDict, total=False):
    route: str
    decision: dict | None
    current_dataset: str | None
    plan: list[str]
    tool_attempts: int
    model_requests: int
    status: str


@dataclass
class AgentRun:
    query: str
    upload_path: str | None = None
    limits: Limits = field(default_factory=Limits.configured)
    planner: object = field(default_factory=Planner)
    use_jev: bool = False
    jev: object = field(default_factory=JevRouter)

    def __post_init__(self):
        self.context = RunContext(self.emit)
        self.id = self.context.run_id
        self.current = None
        self.contract = None
        self.plan = []
        self.conversation = [{'role': 'user', 'content': self.query}]
        self.history = []
        self.attempted = set()
        self.seen_data = {fingerprint(pd.DataFrame())}
        self.seen_metadata = set()
        self.metadata = {}
        self.tool_attempts = self.model_requests = 0
        self.jev_requests = self.jev_selections = self.jev_fallbacks = 0
        self.jev_latency_ms = self.jev_input_tokens = self.jev_output_tokens = 0
        self.jev_cost = None
        self.jev_disabled = False
        self.invalid_decisions = self.tool_failures = self.no_progress = 0
        self.failed_rows = 0
        self.status = 'running'
        self.reason = self.message = ''
        self.initialized = False
        self.busy = False
        self.sink = None
        self.loop = None
        self.final = None
        self.export_count = 0
        self.prepared = None
        self.pending_result = None

    def emit(self, event):
        if self.sink and self.loop:
            # Existing row workers emit from threads. Only the event loop owns queue state.
            def deliver():
                self.sink.put_nowait(clean_value(event))
            self.loop.call_soon_threadsafe(deliver)

    def budget(self):
        result = {'tool_attempts': self.tool_attempts, 'model_requests': self.model_requests,
                'tools_remaining': max(0, self.limits.tool_attempts - self.tool_attempts),
                'models_remaining': max(0, self.limits.model_requests - self.model_requests),
                'consecutive_invalid': self.invalid_decisions,
                'consecutive_failures': self.tool_failures, 'consecutive_no_progress': self.no_progress}
        if self.use_jev:
            result.update(jev_requests=self.jev_requests, planner_requests=self.model_requests - self.jev_requests,
                          jev_selections=self.jev_selections, jev_fallbacks=self.jev_fallbacks,
                          jev_latency_ms=self.jev_latency_ms, jev_input_tokens=self.jev_input_tokens,
                          jev_output_tokens=self.jev_output_tokens, jev_cost=self.jev_cost)
        return result

    def data(self):
        return self.context.snapshots.get(self.current)

    def checks(self):
        schema = self.context.schema_snapshots.get(self.current, DatasetSchema())
        return check_contract(self.contract, self.data(), schema.mode)

    def select_requested_rows(self):
        """Keep the first requested rows only when they satisfy the full contract."""
        df = self.data()
        if self.contract is None or self.contract.max_rows is None or df is None:
            return
        if len(df) <= self.contract.max_rows:
            return
        selected = df.head(self.contract.max_rows).copy()
        schema = self.context.schema_snapshots.get(self.current, DatasetSchema())
        uncapped = self.contract.model_copy(update={'max_rows': None})
        if not check_contract(uncapped, df, schema.mode)['satisfied']:
            return
        if not check_contract(self.contract, selected, schema.mode)['satisfied']:
            return
        snapshot_id = self.current + ':selected'
        self.context.snapshots[snapshot_id] = selected
        self.context.schema_snapshots[snapshot_id] = DatasetSchema(schema.mode, set(selected.columns))
        self.current = snapshot_id
        self.emit({'event': 'notice', 'message': 'Selected the first %s candidates that meet all requested requirements.' % len(selected)})

    def redact(self, error):
        message = str(error)
        for name in ('SIXTYFOUR_API_KEY', 'OPENAI_API_KEY', 'TYPESAFE_API_KEY'):
            key = setting(name)
            if key:
                message = message.replace(key, '[redacted]')
        return message[:2000]

    def stop(self, reason, message, status=None):
        if self.status != 'running':
            return
        self.reason, self.message = reason, message
        self.status = status or ('partial' if self.data() is not None else 'failed')

    def guard(self, before_model=False):
        self.context.check_cancelled()
        if self.status != 'running':
            return False
        for value, limit, reason, message in (
            (self.invalid_decisions, self.limits.invalid_decisions, 'invalid_decisions', 'consecutive invalid decisions'),
            (self.tool_failures, self.limits.tool_failures, 'tool_failures', 'consecutive tool failures'),
            (self.no_progress, self.limits.no_progress, 'no_progress', 'consecutive tool executions without new progress'),
            (self.tool_attempts, self.limits.tool_attempts, 'tool_limit', 'tool attempts'),
        ):
            if value >= limit:
                self.stop(reason, 'Stopped after %s %s.' % (value, message))
                return False
        if before_model and self.model_requests >= self.limits.model_requests:
            self.stop('model_limit', 'Stopped after %s model requests.' % self.model_requests)
            return False
        return True

    def graph_state(self, route='decide', decision=None):
        return {'route': route, 'decision': decision, 'current_dataset': self.current,
                'plan': self.plan, 'tool_attempts': self.tool_attempts,
                'model_requests': self.model_requests, 'status': self.status}

    def model_observation(self):
        data = self.data()
        return {'conversation': self.conversation, 'phase': 'plan' if self.contract is None else 'act',
                'plan': self.plan, 'contract': self.contract.model_dump() if self.contract else None,
                'limits': self.limits.public(), 'budgets': self.budget(),
                'current_dataset': self.current, 'dataset': observation(data) if data is not None else None,
                'snapshots': [{'id': key, 'rows': len(df), 'mode': self.context.schema_snapshots[key].mode,
                               'columns': list(df.columns)[:60]} for key, df in self.context.snapshots.items()],
                'metadata': self.metadata, 'recent_actions': self.history[-12:], 'completion': self.checks()}

    def reject(self, message):
        self.invalid_decisions += 1
        self.history.append({'error': message})
        self.emit({'event': 'decision_rejected', 'message': message, 'budgets': self.budget()})
        self.guard()


async def cancellable(run, awaitable, timeout=None):
    """Interrupt awaiting promptly; underlying synchronous tools observe context cancellation."""
    task = asyncio.ensure_future(awaitable)
    started = asyncio.get_running_loop().time()
    try:
        while not task.done():
            run.context.check_cancelled()
            if timeout and asyncio.get_running_loop().time() - started >= timeout:
                raise TimeoutError('Planner request exceeded its timeout.')
            await asyncio.wait({task}, timeout=0.1)
        run.context.check_cancelled()
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            # Retrieve cancellation without waiting on a possibly uncooperative thread.
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        elif not task.cancelled():
            # Cancellation can arrive after a thread raised but before result() ran.
            task.exception()


def build_graph(run):
    async def route_with_jev():
        if not run.use_jev or run.jev_disabled or run.contract is None:
            return None
        if run.checks()['satisfied']:
            run.stop('criteria_met', 'The result meets the planned completion criteria.', 'completed')
            return None
        # Reserve a request for fallback. Jev and planner share the existing ceiling.
        if run.limits.model_requests - run.model_requests < 2:
            return None
        try:
            options = candidates(run)
        except (ValueError, KeyError, TypeError):
            # Unusual provider values may not support local candidate previews.
            return None
        if not options:
            return None
        run.model_requests += 1
        run.jev_requests += 1
        run.emit({'event': 'thinking', 'message': 'Jev is choosing the next tool…', 'budgets': run.budget()})
        started = time.monotonic()
        selected = None
        confidence = None
        probability = None
        threshold = minimum_probability()
        message = 'Jev deferred to the planner.'
        try:
            selection = await cancellable(run, run.jev.choose(run.model_observation(), options,
                                          min(TIMEOUT, run.limits.model_timeout)),
                                          min(TIMEOUT, run.limits.model_timeout))
            run.jev_input_tokens += selection.input_tokens
            run.jev_output_tokens += selection.output_tokens
            if selection.cost is not None:
                run.jev_cost = (run.jev_cost or 0) + selection.cost
            confidence = selection.confidence
            probability = selection.probability if selection.probability is not None else confidence
            margin = probability - selection.runner_up_probability
            if selection.choice in options and probability >= threshold and margin >= MIN_MARGIN:
                selected = options[selection.choice]
                prepare(run, selected)
                message = 'Jev selected %s (%.0f%% choice probability).' % (selected.tool.replace('_', ' '), probability * 100)
            elif selection.choice == DEFER:
                message = 'Jev requested more planning (%.0f%% choice probability); using the planner.' % (probability * 100)
            elif selection.choice in options:
                reason = ('below the %.0f%% routing threshold' % (threshold * 100) if probability < threshold
                          else 'too close to another option')
                message = 'Jev preferred %s at %.0f%%, %s; using the planner.' % (
                    options[selection.choice].tool.replace('_', ' '), probability * 100, reason)
            else:
                message = 'Jev returned an unknown option; using the planner.'
        except (TimeoutError, ModelError, ValueError, KeyError, TypeError):
            selected = None
            # Avoid repeatedly delaying this run if the provider is unavailable.
            run.jev_disabled = True
            message = 'Jev is unavailable; using the planner for the rest of this run. Check Jev provider credentials and access.'
        finally:
            elapsed = round((time.monotonic() - started) * 1000)
            run.jev_latency_ms += elapsed
        if selected:
            run.jev_selections += 1
        else:
            run.jev_fallbacks += 1
        run.emit({'event': 'jev_decision', 'message': message, 'tool': selected.tool if selected else None,
                  'confidence': confidence, 'probability': probability, 'threshold': threshold,
                  'latency_ms': elapsed, 'budgets': run.budget()})
        return selected

    async def decide(_state):
        run.context.check_cancelled()
        run.select_requested_rows()
        if run.checks()['satisfied']:
            run.stop('criteria_met', 'The result meets the planned completion criteria.', 'completed')
            return run.graph_state('finalize')
        if not run.guard(before_model=True):
            return run.graph_state('finalize')
        decision = await route_with_jev()
        if run.status != 'running':
            return run.graph_state('finalize')
        for retry in range(2):
            if decision is not None:
                break
            if not run.guard(before_model=True):
                return run.graph_state('finalize')
            run.model_requests += 1
            run.emit({'event': 'thinking', 'message': 'Planning the workflow…' if run.contract is None else 'Inspecting results and choosing the next action…', 'budgets': run.budget()})
            try:
                decision = await cancellable(run, run.planner.decide(
                    system_prompt(run.limits, CATALOG_FOR_AGENT), run.model_observation(), run.limits.model_timeout),
                    run.limits.model_timeout)
                if not isinstance(decision, Decision):
                    decision = Decision.model_validate(decision)
                break
            except (TimeoutError, ModelError) as exc:
                if retry == 0 and (isinstance(exc, TimeoutError) or exc.retryable):
                    run.emit({'event': 'notice', 'message': 'Planner request failed; retrying once within the remaining budget.'})
                    continue
                run.stop('model_error', run.redact(exc))
                return run.graph_state('finalize')
            except (ValueError, KeyError, TypeError) as exc:
                run.reject('Invalid planner response: ' + run.redact(exc))
                return run.graph_state('decide' if run.status == 'running' else 'finalize')
        try:
            if run.contract is None and decision.action not in ('plan', 'clarify', 'partial'):
                raise ValueError('Create a completion contract and plan before calling tools or finishing.')
            if run.contract is not None and decision.contract is not None:
                if decision.contract != run.contract:
                    raise ValueError('The completion contract is frozen; it cannot be weakened or replaced.')
            if decision.action == 'plan':
                if run.contract is not None or decision.contract is None or not decision.plan:
                    raise ValueError('Return one initial plan with a completion contract.')
                validate_contract(decision.contract)
                if decision.contract.min_rows > run.limits.max_rows:
                    raise ValueError('Requested count exceeds the row limit. Ask the user to narrow the request.')
                run.contract, run.plan = decision.contract, decision.plan
                if run.current == 'upload' and decision.initial_dataset_mode:
                    run.context.schema_snapshots['upload'].mode = decision.initial_dataset_mode
                run.emit({'event': 'plan', 'message': decision.message, 'steps': run.plan,
                          'contract': run.contract.model_dump()})
            elif decision.action == 'tool':
                if not run.guard():
                    return run.graph_state('finalize')
                run.prepared = prepare(run, decision)
                if decision.plan:
                    run.plan = decision.plan
                    run.emit({'event': 'plan', 'message': 'Updated plan', 'steps': run.plan})
                run.invalid_decisions = 0
                return run.graph_state('execute', decision.model_dump())
            elif decision.action == 'finish':
                checks = run.checks()
                if not checks['satisfied']:
                    raise ValueError('Completion criteria are not met: ' + '; '.join(checks['issues']))
                run.stop('criteria_met', decision.message, 'completed')
            elif decision.action == 'partial':
                run.stop('cannot_complete', decision.message)
            elif decision.action == 'clarify':
                if run.model_requests >= run.limits.model_requests:
                    run.stop('model_limit', 'No model requests remain to process another clarification.')
                else:
                    run.conversation.append({'role': 'assistant', 'content': decision.message})
                    run.stop('clarification', decision.message, 'needs_input')
            run.invalid_decisions = 0
        except (ValueError, KeyError, TypeError) as exc:
            run.reject(run.redact(exc))
        return run.graph_state('decide' if run.status == 'running' else 'finalize')

    async def execute(state):
        if not run.guard():
            return run.graph_state('finalize')
        kind, config, _, _, predicted, signature = run.prepared
        run.tool_attempts += 1
        run.attempted.add(signature)  # Record before submission, including failed requests.
        step_id = 'step-%s' % run.tool_attempts
        run.context.block_id = step_id
        run.context.block_index = run.tool_attempts
        run.emit({'event': 'tool_started', 'id': step_id, 'tool': kind,
                  'message': state['decision']['message'], 'arguments': config, 'budgets': run.budget()})
        try:
            result = await cancellable(run, asyncio.to_thread(execute_prepared, run.prepared, run.context))
            if len(result) > run.limits.max_rows:
                raise ValueError('Tool output exceeded the agent row limit; previous dataset preserved.')
            run.pending_result = (step_id, kind, result, predicted)
        except Cancelled:
            raise
        except Exception as exc:
            message = run.redact(exc)
            run.tool_failures += 1
            run.history.append({'tool': kind, 'error': message})
            run.emit({'event': 'tool_error', 'id': step_id, 'message': message, 'budgets': run.budget()})
            if isinstance(exc, APIError) and exc.status in (401, 402, 403):
                run.stop('provider_access', message)
            run.pending_result = None
        return run.graph_state('inspect')

    async def inspect(_state):
        run.context.check_cancelled()
        if run.pending_result:
            step_id, kind, df, predicted = run.pending_result
            schema = DatasetSchema(predicted.mode, set(df.columns))
            run.context.snapshots[step_id] = df
            run.context.schema_snapshots[step_id] = schema
            failed = int(df.attrs.get('failed_rows', 0))
            run.failed_rows += failed
            run.tool_failures = run.tool_failures + 1 if failed and failed >= len(df) else 0
            data_key = fingerprint(df)
            is_metadata = kind in METADATA_TOOLS
            metadata_key = digest({'data': data_key, 'metadata': run.context.metadata}) if is_metadata else digest(run.context.metadata)
            progress = data_key not in run.seen_data
            if is_metadata or kind == 'inspect_dataset':
                progress = progress or metadata_key not in run.seen_metadata
                run.seen_metadata.add(metadata_key)
                run.metadata[step_id] = {'tool': kind, 'data': observation(df),
                                         'details': json.dumps(clean_value(run.context.metadata), ensure_ascii=False)[:8000]}
            if not is_metadata or (run.contract and run.contract.mode == 'metadata'):
                run.current = step_id
            run.seen_data.add(data_key)
            run.no_progress = 0 if progress else run.no_progress + 1
            summary = {'id': step_id, 'tool': kind, 'row_count': len(df), 'mode': schema.mode,
                       'failed_rows': failed, 'new_progress': progress,
                       'metadata': json.dumps(clean_value(run.context.metadata), ensure_ascii=False)[:3000]}
            run.history.append(summary)
            run.emit({'event': 'tool_done', **summary, **preview(df), 'budgets': run.budget()})
            run.pending_result = None
            # Objective success can finish on the final allowed tool without an extra model call.
            run.select_requested_rows()
            if run.checks()['satisfied']:
                run.stop('criteria_met', 'The result meets the planned completion criteria.', 'completed')
        if run.status == 'running':
            run.guard()
        return run.graph_state('decide' if run.status == 'running' else 'finalize')

    async def finish(_state):
        return run.graph_state('end')

    builder = StateGraph(GraphState)
    builder.add_node('decide', decide)
    builder.add_node('execute', execute)
    builder.add_node('inspect', inspect)
    builder.add_node('finalize', finish)
    builder.add_edge(START, 'decide')
    for node in ('decide', 'execute', 'inspect'):
        builder.add_conditional_edges(node, lambda state: state['route'],
                                      {key: key for key in ('decide', 'execute', 'inspect', 'finalize')})
    builder.add_edge('finalize', END)
    return builder.compile()


async def initialize(run):
    if run.initialized:
        return
    run.initialized = True
    if run.upload_path:
        run.context.check_cancelled()
        run.tool_attempts += 1
        run.context.block_id = 'upload'
        run.emit({'event': 'tool_started', 'id': 'upload', 'tool': 'read_csv', 'message': 'Loading the uploaded CSV.', 'arguments': {}, 'budgets': run.budget()})
        df = await cancellable(run, asyncio.to_thread(run_read, pd.DataFrame(),
                              {'path': run.upload_path, 'delimiter': ','}, run.context))
        if len(df) > run.limits.max_rows:
            raise ValueError('CSV exceeds the agent limit of %s rows. Upload a smaller file.' % run.limits.max_rows)
        df['_row_id'] = ['upload:' + str(i) for i in range(len(df))]
        run.context.snapshots['upload'] = df
        run.context.schema_snapshots['upload'] = DatasetSchema('unknown', set(df.columns))
        run.current = 'upload'
        run.seen_data.add(fingerprint(df))
        run.emit({'event': 'tool_done', 'id': 'upload', 'tool': 'read_csv', **preview(df), 'budgets': run.budget()})


async def finalize(run):
    """One deterministic local export per turn, with no model request or loop re-entry."""
    df = run.data()
    downloads = []
    export_error = None
    if df is not None and len(df.columns):
        run.export_count += 1
        # Separate context allows saving completed snapshots even after cancellation.
        export = RunContext(lambda _: None, threading.Event())
        export.run_id = run.id
        export.block_index = run.export_count
        try:
            await asyncio.to_thread(run_save, df.copy(), {'filename': 'results.csv'}, export)
            downloads = export.downloads
        except Exception as exc:
            export_error = 'Could not export results: ' + run.redact(exc)
    result = preview(df) if df is not None else {'row_count': 0, 'columns': [], 'preview': []}
    run.final = {'event': 'complete', 'run_id': run.id, 'status': run.status,
                 'use_jev': run.use_jev,
                 'reason': run.reason, 'message': run.message, 'checks': run.checks(),
                 'downloads': downloads, 'export_error': export_error, 'failed_rows': run.failed_rows,
                 'budgets': run.budget(), **result}
    run.emit(run.final)


async def execute_run(run):
    try:
        run.emit({'event': 'run_started', 'run_id': run.id, 'use_jev': run.use_jev,
                  'limits': run.limits.public(), 'budgets': run.budget()})
        await initialize(run)
        await build_graph(run).ainvoke(run.graph_state(),
            {'recursion_limit': 4 * (run.limits.tool_attempts + run.limits.model_requests) + 10})
    except (Cancelled, asyncio.CancelledError):
        run.context.cancelled.set()
        run.status = 'cancelled'
        run.reason = 'cancelled'
        run.message = 'Run stopped. Already submitted Sixtyfour jobs may still finish; no further work will be submitted.'
    except GraphRecursionError:
        run.stop('graph_limit', 'Stopped at the graph execution backstop.')
    except Exception as exc:
        run.stop('execution_error', run.redact(exc))
    finally:
        if run.status == 'running':
            run.stop('execution_error', 'Execution ended before completion.')
        await finalize(run)
        run.busy = False
