"""Read a recorded trace, or render an FDB trace as one HTML page.

``render_replay`` prints the coordination-runtime trace with its original
timestamps. ``render_page`` labels heard speech beside executed, dropped, and
reused tool calls. Neither path invents events.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from .metrics import analyze_trace
from .models import TraceEntry


def load_trace(path, scenario=None):
    path = Path(path)
    if path.suffix == '.jsonl':
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    value = json.loads(path.read_text(encoding='utf-8'))
    if isinstance(value, list):
        return value
    if 'trace' in value:
        return value['trace']
    runs = value.get('runs', [])
    matches = [row for row in runs if scenario is None or row.get('scenario_id') == scenario]
    if len(matches) != 1:
        raise ValueError('Select one --scenario from: ' + ', '.join(r['scenario_id'] for r in runs))
    return matches[0]['trace']


def render_replay(rows):
    lines = ['RECORDED TRACE REPLAY - original timestamps; no new inference', 'SECONDS  EVENT / ACTION']
    local = bool(rows and 'timestamp' in rows[0])
    for row in rows:
        timestamp = row.get('timestamp', row.get('t_ms', 0) / 1000)
        kind = row['kind']
        if local:
            data = row.get('data', {})
            if kind == 'INPUT_RECEIVED':
                event = data['event']
                detail = {'type': event['type'], 'payload': event['payload']}
                if 'data_ref' in detail['payload']:
                    detail = {'type': event['type'], 'event_id': event['event_id']}
            elif kind == 'ACTION_EMITTED':
                detail = data['action']
            elif kind in {'STATE_UPDATED', 'CLARIFICATION_UPDATED', 'OBSERVATION_ACCEPTED',
                          'CALL_DISPATCHED', 'CALL_INVALIDATED', 'STALE_RESULT_DISCARDED',
                          'STALE_PLAN_DISCARDED', 'RESULT_ACCEPTED', 'DUPLICATE_WRITE_BLOCKED',
                          'FIRST_RESPONSE_LATENCY', 'CANCEL_EMISSION_LATENCY', 'PLANNER_FAILED',
                          'PERCEPTION_FAILED', 'SESSION_CLOSED'}:
                detail = data
            else:
                continue
        else:
            detail = {k: v for k, v in row.items() if k not in ('kind', 't_ms')}
        serialized = json.dumps(detail, ensure_ascii=True, separators=(',', ':'))
        lines.append(f'{timestamp:7.3f}  {kind} {serialized[:700]}')
    if local:
        metrics = analyze_trace(TraceEntry.model_validate(r) for r in rows)
        lines.extend(['METRICS (seconds)',
            f'first_response_max={metrics.max_first_response_latency}',
            f'cancellation_max={metrics.max_cancellation_latency}',
            f'stale_results_discarded={metrics.stale_results_discarded}',
            f'duplicate_operation_dispatches={metrics.duplicate_operation_dispatches}'])
    return '\n'.join(lines)


PAGE_LABELS = {
    "tool_call": "Executed",
    "tool_call_rechecked": "Re-checked",
    "tool_call_dropped": "Dropped",
    "tool_call_deduplicated": "Reused",
    "transcript": "Heard",
    "conversation_item": "Said",
    "user_state": "Floor",
}


def load_records(path: Path) -> list[dict[str, Any]]:
    records = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def render_page(records: list[dict[str, Any]], title: str = "Interra trace") -> str:
    rows = []
    for record in records:
        kind = str(record.get("kind", ""))
        if kind not in PAGE_LABELS:
            continue
        call = record.get("call") or {}
        detail = record.get("text") or record.get("transcript") or record.get("state") or ""
        if call:
            detail = f"{call.get('function', '')} {json.dumps(call.get('args', {}), ensure_ascii=False)}"
        if record.get("reason"):
            detail = f"{detail} ({record['reason']})"
        rows.append(
            "<tr>"
            f"<td>{html.escape(PAGE_LABELS[kind])}</td>"
            f"<td>{html.escape(str(record.get('room', '')))}</td>"
            f"<td>{html.escape(str(detail))}</td>"
            "</tr>"
        )
    body = "\n".join(rows) or "<tr><td colspan='3'>No speech or tool events in this trace.</td></tr>"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
body {{ font-family: Georgia, serif; margin: 2rem; color: #1c1c1c; background: #fff; }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ border-bottom: 1px solid #ccc; text-align: left; padding: 0.4rem; vertical-align: top; color: #1c1c1c; }}
</style>
</head>
<body>
<h1>{html.escape(title)}</h1>
<table>
<thead><tr><th>Event</th><th>Room</th><th>Detail</th></tr></thead>
<tbody>
{body}
</tbody>
</table>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()
    page = render_page(load_records(args.trace), args.trace.name)
    args.output.write_text(page, encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
