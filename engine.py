"""Evidence-Driven Remediation Engine - Main entry point."""
import argparse
import json
import yaml
from pathlib import Path

from features import extract_features
from retrieval import retrieve_and_vote
from decision import select_action


def decide(incident_path: Path, history_path: Path, actions_path: Path) -> dict:
    """Main decision pipeline: features → retrieval → selection."""
    incident = json.loads(incident_path.read_text())
    history = json.loads(history_path.read_text())
    actions_catalog = yaml.safe_load(actions_path.read_text())
    
    query_vector = extract_features(incident)
    retrieval_result = retrieve_and_vote(query_vector, history, top_k=5)
    decision = select_action(retrieval_result, actions_catalog, query_vector)
    
    return decision


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Evidence-Driven Remediation Engine'
    )
    subparsers = parser.add_subparsers(dest='cmd', help='Commands')
    
    decide_parser = subparsers.add_parser(
        'decide',
        help='Analyze an incident and recommend remediation action'
    )
    decide_parser.add_argument(
        '--incident',
        required=True,
        help='Path to incident JSON file'
    )
    decide_parser.add_argument(
        '--history',
        default='incidents_history.json',
        help='Path to historical incidents JSON'
    )
    decide_parser.add_argument(
        '--actions',
        default='actions.yaml',
        help='Path to actions catalog YAML'
    )
    
    args = parser.parse_args()
    
    if args.cmd == 'decide':
        decision = decide(
            Path(args.incident),
            Path(args.history),
            Path(args.actions)
        )
        
        print(json.dumps(decision, indent=2))
        
        with open('audit.jsonl', 'a') as f:
            f.write(json.dumps(decision) + '\n')
        
        return 0
    else:
        parser.print_help()
        return 1


if __name__ == '__main__':
    import sys
    sys.exit(main())
