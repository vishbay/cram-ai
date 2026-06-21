"""Tests for cram.cost_model."""
import pytest


def test_orientation_caps_at_repo_tokens():
    from cram.cost_model import orientation_tokens
    # If ORIENT_FILES * avg_file > repo_tokens, cap at repo_tokens.
    # 100 files, 10 tok each = 1000 tok repo; orient = min(1000, 8*10) = 80
    result = orientation_tokens(1000, 100)
    assert result <= 1000
    # With a tiny repo where orient would exceed repo size:
    assert orientation_tokens(10, 100) <= 10


def test_orientation_zero_files():
    from cram.cost_model import orientation_tokens
    assert orientation_tokens(0, 0) == 0
    assert orientation_tokens(1000, 0) == 0
    assert orientation_tokens(0, 10) == 0


def test_daily_saving_never_negative():
    from cram.cost_model import CostInputs, daily_costs
    # Even when frozen_tok is large relative to orient
    inp = CostInputs(repo_tokens=100, repo_files=10, frozen_tok=50_000)
    d = daily_costs(inp, 3.0)
    assert d['daily_saving'] >= 0.0


def test_nocram_scales_linearly_with_orient_files(monkeypatch):
    from cram import cost_model
    monkeypatch.setattr(cost_model, 'ORIENT_FILES', 4)
    from cram.cost_model import CostInputs, daily_costs
    inp = CostInputs(repo_tokens=10_000, repo_files=100, frozen_tok=500)
    d4 = daily_costs(inp, 3.0)

    monkeypatch.setattr(cost_model, 'ORIENT_FILES', 8)
    d8 = daily_costs(inp, 3.0)

    # Doubling ORIENT_FILES should double nocram_daily (assuming orient doesn't cap)
    assert abs(d8['nocram_daily'] / d4['nocram_daily'] - 2.0) < 0.01


def test_daily_costs_returns_expected_keys():
    from cram.cost_model import CostInputs, daily_costs
    inp = CostInputs(repo_tokens=50_000, repo_files=50, frozen_tok=2_000)
    d = daily_costs(inp, 3.0)
    assert set(d.keys()) == {'orient_tokens', 'nocram_daily', 'cram_daily', 'daily_saving'}


def test_import_works():
    from cram.cost_model import daily_costs, CostInputs  # noqa: F401


# ---------------------------------------------------------------------------
# budget_status
# ---------------------------------------------------------------------------

class TestBudgetStatus:
    def test_over_budget(self):
        from cram.cost_model import budget_status
        assert budget_status('GOTCHAS.md', 801) == 'over'

    def test_near_budget(self):
        from cram.cost_model import budget_status
        assert budget_status('GOTCHAS.md', 640) == 'near'  # 640 >= 0.8 * 800

    def test_ok_budget(self):
        from cram.cost_model import budget_status
        assert budget_status('GOTCHAS.md', 100) == 'ok'

    def test_unknown_file_none(self):
        from cram.cost_model import budget_status
        assert budget_status('UNKNOWN.md', 9999) == 'none'

    def test_symbols_md_none(self):
        from cram.cost_model import budget_status
        assert budget_status('SYMBOLS.md', 9999) == 'none'

    def test_exact_boundary_over(self):
        from cram.cost_model import budget_status, FILE_BUDGETS
        limit = FILE_BUDGETS['GOTCHAS.md']
        assert budget_status('GOTCHAS.md', limit + 1) == 'over'

    def test_exact_boundary_at_limit_is_ok(self):
        # tokens == limit is not > limit, not near (= exactly 100%), check boundary
        from cram.cost_model import budget_status, FILE_BUDGETS
        limit = FILE_BUDGETS['GOTCHAS.md']
        # tokens == limit: not > limit, >= 0.8*limit → 'near'
        assert budget_status('GOTCHAS.md', limit) == 'near'

    def test_env_override(self, monkeypatch):
        import cram.cost_model as cm
        monkeypatch.setitem(cm.FILE_BUDGETS, 'GOTCHAS.md', 200)
        from cram.cost_model import budget_status
        assert budget_status('GOTCHAS.md', 201) == 'over'
        assert budget_status('GOTCHAS.md', 160) == 'near'
        assert budget_status('GOTCHAS.md', 50) == 'ok'


# ---------------------------------------------------------------------------
# get_provider_pricing
# ---------------------------------------------------------------------------

class TestProviderPricing:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch):
        # Isolate from any pricing env vars set in the developer's shell.
        for var in ('CRAM_PROVIDER', 'CRAM_PRICE_INPUT_PER_MTOK',
                    'CRAM_CACHE_WRITE_MULT', 'CRAM_CACHE_READ_MULT'):
            monkeypatch.delenv(var, raising=False)

    def test_known_providers_return_table_values(self):
        from cram.cost_model import PROVIDER_PRICING, get_provider_pricing
        for name, expected in PROVIDER_PRICING.items():
            assert get_provider_pricing(name) == expected

    def test_default_is_anthropic(self):
        from cram.cost_model import PROVIDER_PRICING, get_provider_pricing
        assert get_provider_pricing() == PROVIDER_PRICING['anthropic']

    def test_env_selects_provider(self, monkeypatch):
        from cram.cost_model import PROVIDER_PRICING, get_provider_pricing
        monkeypatch.setenv('CRAM_PROVIDER', 'gemini')
        assert get_provider_pricing() == PROVIDER_PRICING['gemini']

    def test_unknown_provider_falls_back_to_anthropic(self):
        from cram.cost_model import PROVIDER_PRICING, get_provider_pricing
        assert get_provider_pricing('antropic-typo') == PROVIDER_PRICING['anthropic']

    def test_case_insensitive_lookup(self):
        from cram.cost_model import PROVIDER_PRICING, get_provider_pricing
        assert get_provider_pricing('Anthropic') == PROVIDER_PRICING['anthropic']
        assert get_provider_pricing('OPENAI') == PROVIDER_PRICING['openai']

    def test_env_field_override_wins(self, monkeypatch):
        from cram.cost_model import get_provider_pricing
        monkeypatch.setenv('CRAM_PRICE_INPUT_PER_MTOK', '5.0')
        p = get_provider_pricing('anthropic')
        assert p['input_per_mtok'] == 5.0
        # Other fields untouched.
        assert p['cache_write_mult'] == 1.25

    def test_unparseable_env_override_ignored(self, monkeypatch):
        from cram.cost_model import get_provider_pricing
        monkeypatch.setenv('CRAM_PRICE_INPUT_PER_MTOK', 'not-a-number')
        assert get_provider_pricing('anthropic')['input_per_mtok'] == 3.00

    def test_local_is_all_zeros(self):
        from cram.cost_model import get_provider_pricing
        assert get_provider_pricing('local') == {
            'input_per_mtok': 0.00, 'cache_write_mult': 0.00, 'cache_read_mult': 0.00,
        }

    def test_returns_fresh_dict_each_call(self):
        from cram.cost_model import PROVIDER_PRICING, get_provider_pricing
        p = get_provider_pricing('anthropic')
        p['input_per_mtok'] = 999.0
        assert PROVIDER_PRICING['anthropic']['input_per_mtok'] == 3.00
        assert get_provider_pricing('anthropic')['input_per_mtok'] == 3.00

    def test_legacy_mult_constants_unchanged(self):
        from cram.cost_model import READ_MULT, WRITE_MULT
        assert WRITE_MULT == 1.25
        assert READ_MULT == 0.10


class TestResolveProvider:
    def test_argument_wins(self, monkeypatch):
        from cram.cost_model import resolve_provider
        monkeypatch.setenv('CRAM_PROVIDER', 'openai')
        assert resolve_provider('gemini') == 'gemini'

    def test_env_then_default(self, monkeypatch):
        from cram.cost_model import resolve_provider
        monkeypatch.setenv('CRAM_PROVIDER', 'openai')
        assert resolve_provider() == 'openai'
        monkeypatch.delenv('CRAM_PROVIDER')
        assert resolve_provider() == 'anthropic'

    def test_unknown_falls_back(self):
        from cram.cost_model import resolve_provider
        assert resolve_provider('not-a-provider') == 'anthropic'
        assert resolve_provider('  Gemini ') == 'gemini'

    def test_enterprise_providers_recognised(self):
        from cram.cost_model import resolve_provider
        assert resolve_provider('vertex_ai') == 'vertex_ai'
        assert resolve_provider('bedrock')   == 'bedrock'
        assert resolve_provider('azure')     == 'azure'


class TestEnterpriseProviderPricing:
    """vertex_ai, bedrock, azure share pricing with their base providers."""

    def test_vertex_ai_matches_gemini_pricing(self):
        from cram.cost_model import get_provider_pricing
        assert get_provider_pricing('vertex_ai') == get_provider_pricing('gemini')

    def test_bedrock_matches_anthropic_pricing(self):
        from cram.cost_model import get_provider_pricing
        assert get_provider_pricing('bedrock') == get_provider_pricing('anthropic')

    def test_azure_matches_openai_pricing(self):
        from cram.cost_model import get_provider_pricing
        assert get_provider_pricing('azure') == get_provider_pricing('openai')

    def test_all_providers_have_required_fields(self):
        from cram.cost_model import PROVIDER_PRICING
        required = {'input_per_mtok', 'cache_write_mult', 'cache_read_mult'}
        for name, pricing in PROVIDER_PRICING.items():
            assert required <= pricing.keys(), f'{name} missing fields'

    def test_cram_provider_env_vertex_ai(self, monkeypatch):
        from cram.cost_model import get_provider_pricing
        monkeypatch.setenv('CRAM_PROVIDER', 'vertex_ai')
        p = get_provider_pricing()
        assert p['input_per_mtok'] == 1.25
