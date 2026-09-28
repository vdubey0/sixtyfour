"""Structured filtering plus an interpreted, restricted legacy Pandas subset."""
import ast
import operator
import pandas as pd

OPS = {'eq': operator.eq, 'ne': operator.ne, 'gt': operator.gt, 'gte': operator.ge,
       'lt': operator.lt, 'lte': operator.le}


def legacy_expression(df, expression):
    """Evaluate only explicitly supported AST nodes; never execute Python code."""
    binary = {ast.BitOr: operator.or_, ast.BitAnd: operator.and_}
    compare = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Gt: operator.gt,
               ast.GtE: operator.ge, ast.Lt: operator.lt, ast.LtE: operator.le}

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Name) and node.id == 'df':
            return df
        if isinstance(node, (ast.Str, ast.Num, ast.NameConstant)):
            return getattr(node, 'value', getattr(node, 's', getattr(node, 'n', None)))
        if hasattr(ast, 'Constant') and isinstance(node, ast.Constant):
            if isinstance(node.value, (str, int, float, bool, type(None))):
                return node.value
        if isinstance(node, ast.List):
            return [visit(v) for v in node.elts]
        if isinstance(node, ast.Subscript):
            target = visit(node.value)
            part = node.slice.value if isinstance(node.slice, ast.Index) else node.slice
            key = visit(part)
            if not isinstance(target, (pd.DataFrame, pd.Series)):
                raise ValueError('Only table and column selection is supported')
            return target[key]
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            return binary[type(node.op)](visit(node.left), visit(node.right))
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in compare:
            return compare[type(node.ops[0])](visit(node.left), visit(node.comparators[0]))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Invert):
            return ~visit(node.operand)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            value = visit(node.operand)
            if isinstance(value, (float, int)):
                return -value
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name = node.func.attr
            owner = node.func.value
            string_op = isinstance(owner, ast.Attribute) and owner.attr == 'str'
            target = visit(owner.value if string_op else owner)
            allowed = ('strip', 'lower', 'upper', 'contains', 'startswith', 'endswith') if string_op else ('isna', 'notna', 'isnull', 'notnull', 'isin')
            if not isinstance(target, pd.Series) or name not in allowed:
                raise ValueError('Unsupported legacy method')
            args = [visit(v) for v in node.args]
            if any(k.arg not in ('na', 'case', 'regex') for k in node.keywords):
                raise ValueError('Unsupported legacy argument')
            kwargs = {k.arg: visit(k.value) for k in node.keywords}
            return getattr(target.str if string_op else target, name)(*args, **kwargs)
        raise ValueError('Unsupported expression. Use structured rules or the documented legacy subset.')

    result = visit(ast.parse(expression, mode='eval'))
    if not isinstance(result, pd.DataFrame):
        raise ValueError('Legacy expression must return a table, e.g. df[df["email"].notna()]')
    return result.copy()


def validate_rules(rules):
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get('column'), str) or not rule['column'].strip():
            raise ValueError('Each filter rule needs a column')
        op = rule.get('operator')
        if op not in set(OPS) | {'in', 'not_in', 'contains', 'is_missing', 'is_present'}:
            raise ValueError('Unknown filter operator: ' + str(op))
        if op in ('in', 'not_in') and not isinstance(rule.get('value'), list):
            raise ValueError('in/not_in require an array value')
        if op not in ('is_missing', 'is_present') and 'value' not in rule:
            raise ValueError('Filter value is required')


def filter(df, config, context=None):
    if config.get('rule', '').strip():
        return legacy_expression(df, config['rule'])
    rules = config['rules']
    validate_rules(rules)
    masks = []
    for rule in rules:
        name, op, value = rule['column'], rule['operator'], rule.get('value')
        if name not in df.columns:
            raise ValueError('Missing filter column: ' + name)
        series = df[name]
        blank = series.isna() | series.astype(str).str.strip().eq('')
        if op in ('is_missing', 'is_present'):
            mask = blank if op == 'is_missing' else ~blank
        elif op in ('in', 'not_in'):
            mask = series.isin(value)
            if op == 'not_in':
                mask = ~mask
            mask &= ~blank
        elif op == 'contains':
            mask = series.astype(str).str.contains(str(value), regex=False, na=False) & ~blank
        else:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                series = pd.to_numeric(series, errors='coerce')
            mask = OPS[op](series, value) & ~blank & series.notna()
        masks.append(mask.fillna(False))
    result = pd.Series(config['match'] == 'all', index=df.index)
    for mask in masks:
        result = result & mask if config['match'] == 'all' else result | mask
    return df.loc[result].copy()
