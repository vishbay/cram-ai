"""Tests for the recommendation registry (cram/recommend.py) and its wiring
into audit findings — the typed waste-class → optimizer mapping that turns
cram's prose findings into structured, verifiable recommendations."""

from __future__ import annotations

from cram.recommend import (
    OPTIMIZERS, WASTE_CLASS_OF, CLASS_OPTIMIZERS,
    waste_class_for, recommend_for, attach_recommendations,
)
from cram.audit_findings import derive_findings


# ── Registry integrity ───────────────────────────────────────────────────────

class TestRegistryIntegrity:
    def test_every_finding_class_maps_to_optimizers(self):
        # Every waste class referenced by a finding has at least one optimizer.
        for cls in set(WASTE_CLASS_OF.values()):
            assert CLASS_OPTIMIZERS.get(cls), f'no optimizers for class {cls}'

    def test_class_optimizers_reference_real_ids(self):
        for cls, ids in CLASS_OPTIMIZERS.items():
            assert ids, f'{cls} has no optimizers'
            for oid in ids:
                assert oid in OPTIMIZERS, f'{cls} → unknown optimizer {oid}'

    def test_optimizer_addresses_match_class_mapping(self):
        # If a class lists an optimizer, that optimizer claims to address it.
        for cls, ids in CLASS_OPTIMIZERS.items():
            for oid in ids:
                assert cls in OPTIMIZERS[oid].addresses

    def test_kinds_are_known(self):
        assert {o.kind for o in OPTIMIZERS.values()} <= {'cram', 'config', 'external'}

    def test_coding_agent_scope_no_inference_path_optimizers(self):
        # Persona guard: nothing that needs you to own the inference path.
        banned = ('gptcache', 'redis', 'litellm', 'bifrost', 'lmcache', 'vllm', 'gateway')
        for oid in OPTIMIZERS:
            assert not any(b in oid.lower() for b in banned), f'out-of-scope: {oid}'


# ── Lookups ──────────────────────────────────────────────────────────────────

class TestLookups:
    def test_waste_class_for_known_and_unknown(self):
        assert waste_class_for('repeated-reads') == 'orientation'
        assert waste_class_for('nope') is None

    def test_recommend_for_orientation_is_cram_layer(self):
        rec = recommend_for('high-orientation')
        assert rec['optimizer'] == 'cram-context-layer'
        assert rec['kind'] == 'cram'
        assert rec['alternatives'] == []

    def test_recommend_for_bloat_primary_is_zero_dep_with_llmlingua_alt(self):
        # Lowest-dependency option leads; LLMLingua trails as the alternative.
        rec = recommend_for('oversized-results')
        assert rec['optimizer'] == 'output-protection'
        assert 'llmlingua-tool-output' in rec['alternatives']

    def test_recommend_for_unknown_is_none(self):
        assert recommend_for('does-not-exist') is None

    def test_recommendation_is_json_friendly(self):
        import json
        json.dumps(recommend_for('cache-blind'))  # must not raise


# ── Detector signatures (verify-loop seam) ───────────────────────────────────

class TestDetectors:
    def test_cram_layer_has_get_context_signature(self):
        det = OPTIMIZERS['cram-context-layer'].detector
        assert det == {'kind': 'mcp_tool', 'match': 'get_context'}

    def test_unwired_optimizers_have_no_detector_yet(self):
        assert OPTIMIZERS['llmlingua-tool-output'].detector is None


# ── attach_recommendations ───────────────────────────────────────────────────

class TestAttach:
    def test_annotates_in_place_additively(self):
        findings = [{'id': 'repeated-reads', 'severity': 'warn',
                     'evidence': 'e', 'fix': 'f'}]
        out = attach_recommendations(findings)
        assert out is findings
        f = findings[0]
        assert f['evidence'] == 'e' and f['fix'] == 'f'   # untouched
        assert f['waste_class'] == 'orientation'
        assert f['recommended']['optimizer'] == 'cram-context-layer'

    def test_unmapped_finding_gets_none_not_dropped(self):
        findings = [{'id': 'mystery', 'severity': 'warn', 'evidence': 'e', 'fix': 'f'}]
        attach_recommendations(findings)
        assert findings[0]['waste_class'] is None
        assert findings[0]['recommended'] is None


# ── Integration with derive_findings ─────────────────────────────────────────

def _data(**over):
    base = dict(
        sessions=10, top_read_files=[('hot.py', 9, 4)], pre_edit_spend_share=None,
        pre_edit_measured_sessions=0, sessions_with_big_results=0,
        big_result_bytes=20000, carried_cost_per_session=0.0,
        cache_blind_sessions=0, avg_error_results=0.0, sessions_with_errors=0,
        avg_edit_churn=0.0, avg_context_growth=None, context_growth_measured=0,
    )
    base.update(over)
    return base


class TestDeriveFindingsCarriesRecommendations:
    def test_finding_has_waste_class_and_recommended(self):
        f = derive_findings(_data())
        assert [x['id'] for x in f] == ['repeated-reads']
        assert f[0]['waste_class'] == 'orientation'
        assert f[0]['recommended']['optimizer'] == 'cram-context-layer'

    def test_every_real_finding_is_classified(self):
        # A data blob that trips multiple findings; all must be classified.
        f = derive_findings(_data(
            sessions_with_big_results=3, carried_cost_per_session=0.04,
            cache_blind_sessions=2, avg_error_results=2.0, sessions_with_errors=5,
            avg_edit_churn=3.0,
        ))
        assert len(f) >= 4
        for x in f:
            assert x['waste_class'] is not None
            assert x['recommended'] is not None
