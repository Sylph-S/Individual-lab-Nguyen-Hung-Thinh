"""Layer 2: Similarity-based retrieval and outcome-weighted voting."""
import math
from typing import Dict, List, Any, Tuple
from features import normalize_log_message


def jaccard_similarity(set_a: set, set_b: set) -> float:
    """Compute Jaccard similarity between two sets."""
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def cosine_similarity(vec_a: Dict[str, float], vec_b: Dict[str, float]) -> float:
    """Compute cosine similarity between two sparse vectors."""
    if not vec_a or not vec_b:
        return 0.0
    
    common_keys = set(vec_a.keys()) & set(vec_b.keys())
    if not common_keys:
        return 0.0
    
    dot_product = sum(vec_a[k] * vec_b[k] for k in common_keys)
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
    
    if norm_a == 0 or norm_b == 0:
        return 0.0
    
    return dot_product / (norm_a * norm_b)


def compare_traces(query_traces: Dict, hist_traces: List[Dict]) -> float:
    """Compare trace signatures: edge-wise error rates."""
    if not query_traces or not hist_traces:
        return 0.0
    
    hist_edges = {}
    for sig in hist_traces:
        edge = f"{sig.get('from', '')}→{sig.get('to', '')}"
        hist_edges[edge] = {
            'p99_deviation_ratio': sig.get('p99_deviation_ratio', 1.0),
            'error_rate': sig.get('error_rate', 0.0)
        }
    
    query_edges = {}
    for edge_key, features in query_traces.items():
        query_edges[edge_key] = {
            'error_rate': features.get('error_rate', 0.0),
            'p99_ratio': features.get('ratio', 1.0)
        }
    
    common_edges = set(query_edges.keys()) & set(hist_edges.keys())
    if not common_edges:
        query_services = {edge.split('→')[i] for edge in query_edges for i in [0, 1]}
        hist_services = {edge.split('→')[i] for edge in hist_edges for i in [0, 1]}
        return 0.3 * jaccard_similarity(query_services, hist_services)
    
    similarities = []
    for edge in common_edges:
        q = query_edges[edge]
        h = hist_edges[edge]
        error_diff = abs(q['error_rate'] - h['error_rate'])
        error_sim = max(0, 1.0 - error_diff)
        similarities.append(error_sim)
    
    return sum(similarities) / len(similarities) if similarities else 0.0


def compare_logs(query_logs: Dict[str, int], hist_logs: List[str]) -> float:
    """Compare log templates via Jaccard on normalized signatures."""
    if not query_logs or not hist_logs:
        return 0.0
    
    hist_templates = {normalize_log_message(sig) for sig in hist_logs}
    query_templates = set(query_logs.keys())
    
    return jaccard_similarity(query_templates, hist_templates)


def compare_services(query_services: List[str], hist_services: List[str]) -> float:
    """Compare affected services overlap."""
    if not query_services or not hist_services:
        return 0.0
    return jaccard_similarity(set(query_services), set(hist_services))


def similarity(query_vector: Dict, historical_incident: Dict) -> float:
    """
    Compute overall similarity: 40% logs + 40% traces + 20% services.
    """
    query_logs = query_vector.get('log_templates', {})
    query_traces = query_vector.get('trace_features', {})
    query_services = query_vector.get('affected_services', [])
    
    hist_logs = historical_incident.get('log_signatures', [])
    hist_traces = historical_incident.get('trace_signatures', [])
    hist_services = historical_incident.get('affected_services', [])
    
    log_sim = compare_logs(query_logs, hist_logs)
    trace_sim = compare_traces(query_traces, hist_traces)
    service_sim = compare_services(query_services, hist_services)
    
    total_sim = 0.4 * log_sim + 0.4 * trace_sim + 0.2 * service_sim
    
    return total_sim


def parse_action_from_history(action_str: str) -> Dict[str, Any]:
    """Parse 'rollback_service:payment-svc:v3.1' into structured form."""
    parts = action_str.split(':')
    if not parts:
        return {'name': 'page_oncall', 'params': {}}
    
    name = parts[0]
    params = parts[1:] if len(parts) > 1 else []
    
    param_map = {
        'rollback_service': ['service', 'target_version'],
        'increase_pool_size': ['service', 'from_value', 'to_value'],
        'restart_pod': ['service', 'pod_selector'],
        'dns_config_rollback': ['configmap_name', 'target_revision'],
        'network_policy_revert': ['policy_name'],
        'page_oncall': ['team']
    }
    
    param_names = param_map.get(name, [])
    named_params = {}
    for i, value in enumerate(params):
        if i < len(param_names):
            named_params[param_names[i]] = value
    
    return {'name': name, 'params': named_params}


def retrieve_and_vote(query_vector: Dict, history: List[Dict], 
                      top_k: int = 5, similarity_threshold: float = 0.15) -> Dict[str, Any]:
    """Layer 2: kNN retrieval with outcome-weighted voting."""
    similarities = []
    for hist_incident in history:
        sim = similarity(query_vector, hist_incident)
        similarities.append((hist_incident, sim))
    
    similarities.sort(key=lambda x: x[1], reverse=True)
    max_similarity = similarities[0][1] if similarities else 0.0
    neighbors = [(inc, sim) for inc, sim in similarities[:top_k] if sim >= similarity_threshold]
    
    if not neighbors:
        return {
            'candidates': {},
            'neighbors': [],
            'max_similarity': max_similarity,
            'is_ood': True
        }
    
    outcome_weights = {
        'success': 1.0,
        'partial': 0.5,
        'failed': 0.1
    }
    
    action_votes = {}
    
    for hist_incident, sim_score in neighbors:
        outcome = hist_incident.get('outcome', 'success')
        outcome_weight = outcome_weights.get(outcome, 0.5)
        vote_weight = sim_score * outcome_weight
        
        actions_taken = hist_incident.get('actions_taken', [])
        for action_str in actions_taken:
            action = parse_action_from_history(action_str)
            action_key = action['name']
            
            if 'service' in action['params']:
                action_key = f"{action['name']}:{action['params']['service']}"
            
            if action_key not in action_votes:
                action_votes[action_key] = {
                    'action': action,
                    'total_weight': 0.0,
                    'success_weight': 0.0,
                    'count': 0,
                    'incidents': []
                }
            
            action_votes[action_key]['total_weight'] += vote_weight
            if outcome == 'success':
                action_votes[action_key]['success_weight'] += vote_weight
            action_votes[action_key]['count'] += 1
            action_votes[action_key]['incidents'].append({
                'id': hist_incident.get('id'),
                'similarity': sim_score,
                'outcome': outcome
            })
    
    sorted_candidates = sorted(
        action_votes.items(),
        key=lambda x: x[1]['total_weight'],
        reverse=True
    )
    
    return {
        'candidates': dict(sorted_candidates),
        'neighbors': [(inc, sim, inc.get('outcome')) for inc, sim in neighbors],
        'max_similarity': max_similarity,
        'is_ood': False
    }
