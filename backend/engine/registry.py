"""One execution registry for all blocks in the existing workflow engine."""
from backend.blocks.inspect_dataset import inspect_dataset
from backend.blocks.deduplicate_rows import deduplicate_rows
from backend.blocks.merge_datasets import merge_datasets
from backend.blocks.research_companies import research_companies
from backend.blocks.find_decision_makers import find_decision_makers
from backend.blocks.find_phone import find_phone
from backend.blocks.reverse_email import reverse_email
from backend.blocks.enrich_linkedin import enrich_linkedin
from backend.blocks.qualify_leads import qualify_leads
from backend.blocks.find_prospects import find_prospects
from backend.blocks.search_by_filters import search_by_filters
from backend.blocks.get_search_fields import get_search_fields
from backend.blocks.get_search_field_values import get_search_field_values
from backend.blocks.enrich_lead import enrich_lead
from backend.blocks.find_email import find_email
from backend.blocks.filter import filter
from backend.blocks.read_csv import run_read
from backend.blocks.save_csv import run_save

# Map stable UI type identifiers to callables taking (df, config, context).
# Adding a tool requires a catalog definition and an entry in this registry.
_registry = {}


class UnknownBlockTypeError(ValueError):
    pass


def register(block_type, fn):
    """Associate a tool identifier with its execution function."""
    _registry[block_type] = fn


def get_block_function(block_type):
    """Resolve a canvas block type; unknown names fail explicitly."""
    if block_type not in _registry:
        raise UnknownBlockTypeError('Unknown block type: ' + block_type)
    return _registry[block_type]


register('read_csv', run_read)
register('save_csv', run_save)
register('enrich_lead', enrich_lead)
register('find_email', find_email)
register('filter', filter)
register('inspect_dataset', inspect_dataset)
register('deduplicate_rows', deduplicate_rows)
register('merge_datasets', merge_datasets)
register('research_companies', research_companies)
register('find_decision_makers', find_decision_makers)
register('find_phone', find_phone)
register('reverse_email', reverse_email)
register('enrich_linkedin', enrich_linkedin)
register('qualify_leads', qualify_leads)
register('find_prospects', find_prospects)
register('search_by_filters', search_by_filters)
register('get_search_fields', get_search_fields)
register('get_search_field_values', get_search_field_values)
