"""Layer 1: Feature extraction from incident evidence."""
import re
from collections import Counter
from typing import Dict, List, Any


def normalize_log_message(msg: str) -> str:
    """Convert raw log line into template by removing variable parts."""
    msg = re.sub(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?', '<TIMESTAMP>', msg)
    msg = re.sub(r'\b\d+(?:\.\d+)?\b(?:ms|s|MB|KB|%)?', '<NUM>', msg)
    msg = re.sub(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', '<UUID>', msg, flags=re.IGNORECASE)
    msg = re.sub(r'\b0x[0-9a-f]+\b', '<HEX>', msg, flags=re.IGNORECASE)
    msg = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '<IP>', msg)
    msg = ' '.join(msg.split())
    return msg.lower()


def cluster_logs(logs: List[Dict]) -> Dict[str, int]:
    """Group logs into templates with frequency counts."""
    templates = Counter()
    for log_entry in logs:
        template = normalize_log_message(log_entry.get('msg', ''))
        templates[template] += 1
    return dict(templates)


def extract_trace_features(traces: List[Dict]) -> Dict[str, Any]:
    """Aggregate trace data per edge: error rates and latency stats."""
    edge_features = {}
    
    for trace in traces:
        edge_key = f"{trace.get('from', 'unknown')}→{trace.get('to', 'unknown')}"
        
        count = trace.get('count', 0)
        error_count = trace.get('error_count', 0)
        p99_ms = trace.get('p99_ms', 0)
        p50_ms = trace.get('p50_ms', 0)
        
        error_rate = error_count / count if count > 0 else 0
        
        if edge_key not in edge_features:
            edge_features[edge_key] = {
                'total_count': 0,
                'total_errors': 0,
                'max_p99': 0,
                'sum_p99': 0,
                'samples': 0
            }
        
        edge_features[edge_key]['total_count'] += count
        edge_features[edge_key]['total_errors'] += error_count
        edge_features[edge_key]['max_p99'] = max(edge_features[edge_key]['max_p99'], p99_ms)
        edge_features[edge_key]['sum_p99'] += p99_ms
        edge_features[edge_key]['samples'] += 1
    
    for edge_key in edge_features:
        ef = edge_features[edge_key]
        ef['error_rate'] = ef['total_errors'] / ef['total_count'] if ef['total_count'] > 0 else 0
        ef['avg_p99'] = ef['sum_p99'] / ef['samples'] if ef['samples'] > 0 else 0
    
    return edge_features


def extract_metric_features(metrics_window: Dict) -> Dict[str, Any]:
    """Split metrics into baseline and recent windows, compute deltas."""
    samples = metrics_window.get('samples', {})
    metric_features = {}
    
    for metric_name, timeseries in samples.items():
        if not timeseries or len(timeseries) < 2:
            continue
        
        values = [point[1] for point in timeseries]
        
        split_idx = max(1, int(len(values) * 0.4))
        baseline = values[:split_idx]
        recent = values[split_idx:]
        
        baseline_avg = sum(baseline) / len(baseline)
        recent_avg = sum(recent) / len(recent)
        
        delta = recent_avg - baseline_avg
        ratio = recent_avg / baseline_avg if baseline_avg > 0 else 1.0
        
        metric_features[metric_name] = {
            'baseline': baseline_avg,
            'recent': recent_avg,
            'delta': delta,
            'ratio': ratio,
            'max': max(values),
            'min': min(values)
        }
    
    return metric_features


def derive_affected_services(incident: Dict) -> List[str]:
    """Identify affected services from trigger, high-error traces, and ERROR logs."""
    affected = set()
    
    trigger = incident.get('trigger_alert', {})
    if 'service' in trigger:
        affected.add(trigger['service'])
    
    traces = incident.get('traces', [])
    for trace in traces:
        error_count = trace.get('error_count', 0)
        count = trace.get('count', 1)
        error_rate = error_count / count if count > 0 else 0
        
        if error_rate > 0.1:
            affected.add(trace.get('from', ''))
            affected.add(trace.get('to', ''))
        
        if trace.get('p99_ms', 0) > 1000:
            affected.add(trace.get('from', ''))
            affected.add(trace.get('to', ''))
    
    logs = incident.get('logs', [])
    error_services = [log.get('svc') for log in logs if log.get('level') == 'ERROR']
    affected.update(error_services)
    
    return sorted([svc for svc in affected if svc])


def extract_features(incident: Dict) -> Dict[str, Any]:
    """Layer 1: Convert raw incident into comparable feature vector."""
    logs = incident.get('logs', [])
    traces = incident.get('traces', [])
    metrics_window = incident.get('metrics_window', {})
    trigger_alert = incident.get('trigger_alert', {})
    
    return {
        'incident_id': incident.get('incident_id', 'unknown'),
        'trigger_service': trigger_alert.get('service', 'unknown'),
        'trigger_rule': trigger_alert.get('rule_id', 'unknown'),
        'trigger_severity': trigger_alert.get('severity', 'unknown'),
        'log_templates': cluster_logs(logs),
        'trace_features': extract_trace_features(traces),
        'metric_features': extract_metric_features(metrics_window),
        'affected_services': derive_affected_services(incident),
        'raw_incident': incident
    }
