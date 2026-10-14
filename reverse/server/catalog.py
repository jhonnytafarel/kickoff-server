"""Runtime IDs from the source-only compatibility metadata."""
from resource_catalog import RESOURCE
ITEMS={int(k):v for k,v in RESOURCE['items'].items()}
SKILLS={int(k):v for k,v in RESOURCE['skills'].items()}
TUTORIAL_IDS=frozenset(RESOURCE['tutorials'])
