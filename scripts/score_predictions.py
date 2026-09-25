"""Score a completed checkpoint: python scripts/score_predictions.py RUN_DIRECTORY."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from saas_bench.db_protection import load_session_db
from saas_bench.run_state import checkpoint_directory, write_json
from saas_bench.scoring import score_predictions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--outcome', choices=['running', 'completed', 'bankrupt', 'failed', 'timeout'], default='running')
    parser.add_argument('--submit-start', type=int)
    parser.add_argument('--submit-end', type=int)
    args = parser.parse_args()
    checkpoint = json.loads((args.run / 'checkpoint.json').read_text())
    directory = checkpoint_directory(args.run, checkpoint)
    manifest = json.loads((args.run / 'manifest.json').read_text())
    conn = load_session_db(directory / 'world.nmdb')
    try:
        result = score_predictions(conn, checkpoint['day'], outcome=args.outcome,
            planned_end_day=manifest['configuration']['total_days'],
            submit_start=args.submit_start, submit_end=args.submit_end)
    finally:
        conn.close()
    write_json(args.run / 'prediction_scores.json', result)
    print(json.dumps({key: value for key, value in result.items() if key != 'rows'}, indent=2))


if __name__ == '__main__':
    main()
