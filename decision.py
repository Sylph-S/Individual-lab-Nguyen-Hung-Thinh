"""Layer 3: Cost-aware action selection with blast-radius gating."""
from typing import Dict, List, Any, Optional


def normalize_incident_id(inc_id: str) -> str:
    """Extract short ID like 'E01' from 'E01-2026-06-10-001'."""
    if '-' in inc_id:
        return inc_id.split('-')[0]
    return inc_id


def find_action_metadata(action_name: str, actions_catalog: List[Dict]) -> Optional[Dict]:
    """Look up action metadata from catalog."""
    for action in actions_catalog:
        if action['name'] == action_name:
            return action
    return None


def calculate_expected_value(candidate: Dict, action_meta: Dict, 
                             confidence: float) -> float:
    """Calculate EV = P(success) × benefit - cost."""
    total_weight = candidate.get('total_weight', 0)
    success_weight = candidate.get('success_weight', 0)
    
    p_success = success_weight / total_weight if total_weight > 0 else 0
    adjusted_confidence = (confidence + p_success) / 2.0
    
    cost_min = action_meta.get('cost_min', 0)
    downtime_min = action_meta.get('downtime_min', 0)
    
    benefit = 100 - (cost_min * 2) - (downtime_min * 5)
    ev = adjusted_confidence * benefit - (1 - adjusted_confidence) * (cost_min + downtime_min * 2)
    
    return ev


def compute_confidence(candidate: Dict, max_similarity: float, 
                       neighbor_count: int) -> float:
    """Compute confidence from vote weight, similarity, agreement, success rate."""
    total_weight = candidate.get('total_weight', 0)
    count = candidate.get('count', 0)
    success_weight = candidate.get('success_weight', 0)
    
    weight_confidence = min(1.0, total_weight / (neighbor_count * 0.8))
    sim_confidence = max_similarity
    agreement_confidence = min(1.0, count / max(1, neighbor_count * 0.5))
    success_rate = success_weight / total_weight if total_weight > 0 else 0.5
    
    confidence = (
        0.3 * weight_confidence +
        0.3 * sim_confidence +
        0.2 * agreement_confidence +
        0.2 * success_rate
    )
    
    return min(1.0, max(0.0, confidence))


def should_escalate(candidates: Dict, max_similarity: float, 
                    best_confidence: float, best_ev: float,
                    best_action_meta: Dict) -> bool:
    """Decide whether to escalate to oncall instead of auto-acting."""
    if not candidates:
        return True
    
    if max_similarity < 0.2:
        return True
    
    if best_confidence < 0.35:
        return True
    
    blast_radius = best_action_meta.get('blast_radius_services', 0)
    if blast_radius >= 3 and best_confidence < 0.6:
        return True
    
    if best_ev < 0:
        return True
    
    return False


def select_action(retrieval_result: Dict, actions_catalog: List[Dict],
                  query_vector: Dict) -> Dict[str, Any]:
    """Layer 3: Select best action with cost-awareness and risk gating."""
    candidates = retrieval_result.get('candidates', {})
    neighbors = retrieval_result.get('neighbors', [])
    max_similarity = retrieval_result.get('max_similarity', 0.0)
    is_ood = retrieval_result.get('is_ood', False)
    
    incident_id = normalize_incident_id(query_vector.get('incident_id', 'unknown'))
    
    if is_ood or not candidates:
        return {
            'incident_id': incident_id,
            'selected_action': 'page_oncall',
            'params': {'team': 'platform-team'},
            'confidence': 1.0,
            'evidence': {
                'reason': 'out_of_distribution',
                'max_similarity': max_similarity,
                'neighbors_found': len(neighbors),
                'explanation': 'No similar historical incidents found. Escalating to on-call.'
            }
        }
    
    candidate_scores = []
    for action_key, candidate in candidates.items():
        action = candidate['action']
        action_name = action['name']
        
        if action_name == 'page_oncall':
            continue
        
        action_meta = find_action_metadata(action_name, actions_catalog)
        if not action_meta:
            continue
        
        confidence = compute_confidence(candidate, max_similarity, len(neighbors))
        ev = calculate_expected_value(candidate, action_meta, confidence)
        
        candidate_scores.append({
            'action_key': action_key,
            'action': action,
            'candidate': candidate,
            'action_meta': action_meta,
            'confidence': confidence,
            'ev': ev
        })
    
    candidate_scores.sort(key=lambda x: x['ev'], reverse=True)
    
    if not candidate_scores:
        return {
            'incident_id': incident_id,
            'selected_action': 'page_oncall',
            'params': {'team': 'platform-team'},
            'confidence': 0.8,
            'evidence': {
                'reason': 'no_auto_action_candidates',
                'max_similarity': max_similarity,
                'explanation': 'Only escalation actions available in historical matches.'
            }
        }
    
    best = candidate_scores[0]
    best_action = best['action']
    best_confidence = best['confidence']
    best_ev = best['ev']
    best_meta = best['action_meta']
    
    if should_escalate(candidates, max_similarity, best_confidence, best_ev, best_meta):
        escalation_reason = []
        if max_similarity < 0.2:
            escalation_reason.append(f'low_similarity (max={max_similarity:.2f})')
        if best_confidence < 0.35:
            escalation_reason.append(f'low_confidence ({best_confidence:.2f})')
        if best_ev < 0:
            escalation_reason.append(f'negative_ev ({best_ev:.2f})')
        if best_meta.get('blast_radius_services', 0) >= 3 and best_confidence < 0.6:
            escalation_reason.append(f'high_blast_radius_with_uncertainty')
        
        return {
            'incident_id': incident_id,
            'selected_action': 'page_oncall',
            'params': {'team': 'platform-team'},
            'confidence': 1.0,
            'evidence': {
                'reason': 'escalation_gate_triggered',
                'triggers': escalation_reason,
                'best_auto_action': best_action['name'],
                'best_auto_confidence': best_confidence,
                'best_auto_ev': best_ev,
                'max_similarity': max_similarity,
                'explanation': f"Escalating due to: {', '.join(escalation_reason)}"
            }
        }
    
    evidence = {
        'reason': 'auto_action',
        'max_similarity': max_similarity,
        'confidence': best_confidence,
        'expected_value': best_ev,
        'alternatives_considered': len(candidate_scores),
        'top_neighbors': [
            {
                'incident_id': inc.get('id'),
                'similarity': sim,
                'outcome': outcome
            }
            for inc, sim, outcome in neighbors[:3]
        ],
        'voting_summary': {
            'total_weight': best['candidate']['total_weight'],
            'success_weight': best['candidate']['success_weight'],
            'vote_count': best['candidate']['count']
        },
        'explanation': f"Auto-recommending {best_action['name']} with {best_confidence:.1%} confidence based on {len(neighbors)} similar historical incidents."
    }
    
    params = best_action['params'].copy()
    if best_action['name'] == 'rollback_service' and 'target_version' not in params:
        params['target_version'] = 'previous'
    
    if best_action['name'] == 'increase_pool_size':
        if 'from_value' not in params:
            params['from_value'] = '50'
        if 'to_value' not in params:
            params['to_value'] = '100'
    
    return {
        'incident_id': incident_id,
        'selected_action': best_action['name'],
        'params': params,
        'confidence': best_confidence,
        'evidence': evidence
    }
