"""Generate Rasa NLU/rules from reviewed per-document intent mappings."""
import json
from pathlib import Path
import yaml
ROOT = Path(__file__).resolve().parents[1]

def main():
    mapping = json.loads((ROOT / 'data/policy_intents.json').read_text())
    nlu = ['version: "3.1"', 'nlu:']
    rules = ['version: "3.1"', 'rules:']
    for name, item in mapping.items():
        nlu.extend([f'- intent: {name}', '  examples: |'])
        nlu.extend(f'    - {example}' for example in item['examples'])
        rules.extend([f'- rule: Route {name}', '  steps:', f'    - intent: {name}', '    - action: action_answer_policy'])
    (ROOT / 'data/policy_nlu.yml').write_text('\n'.join(nlu)+'\n')
    (ROOT / 'data/policy_rules.yml').write_text('\n'.join(rules)+'\n')
    domain_path = ROOT / 'domain.yml'
    domain = yaml.safe_load(domain_path.read_text())
    domain['intents'] = [i for i in domain['intents'] if not i.startswith('policy_')] + list(mapping)
    domain_path.write_text(yaml.safe_dump(domain, sort_keys=False, allow_unicode=True))
    base_path = ROOT / 'data/nlu.yml'
    base = yaml.safe_load(base_path.read_text())
    specific = {q.casefold() for route in mapping.values() for q in route['examples']}
    for entry in base['nlu']:
        if 'examples' in entry:
            entry['examples'] = '\n'.join(line for line in entry['examples'].splitlines() if line.lstrip('- ').casefold() not in specific) + '\n'
    # Keep readable block-style utterances when removing exact label conflicts.
    class Dumper(yaml.SafeDumper):
        pass
    def represent_string(dumper, value):
        return dumper.represent_scalar('tag:yaml.org,2002:str', value, style='|' if '\n' in value else None)
    Dumper.add_representer(str, represent_string)
    base_path.write_text(yaml.dump(base, Dumper=Dumper, sort_keys=False, allow_unicode=True))
    print(f'Synchronized {len(mapping)} document intents; run make train before using them.')

if __name__ == '__main__':
    main()
