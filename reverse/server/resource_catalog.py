"""Load compatibility metadata without requiring a client installation."""
import json
from pathlib import Path

PATH=Path(__file__).parent/'resources/game_catalog.json'
def load(path=PATH):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('schema_version')!=1:raise ValueError('Unsupported compatibility catalog schema')
    required=('items','skills','tutorials','goods','selections','levels','profiles','item_info','missions','goalkeepers')
    if any(not data.get(key) for key in required):raise ValueError('Incomplete compatibility catalog')
    return data

RESOURCE=load()
