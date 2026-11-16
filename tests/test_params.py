import numpy as np
import pytest

from ffp import params


@pytest.mark.parametrize("kind", ["within", "pooled"])
@pytest.mark.parametrize("position", ["WR", "TE"])
def test_correlation_matrices_are_valid(position, kind):
    stats, r = params.correlation(position, kind)
    assert stats[:3] == ["receptions", "receiving_yards", "receiving_tds"]
    assert r.shape == (len(stats), len(stats))
    np.testing.assert_allclose(r, r.T)
    np.testing.assert_allclose(np.diag(r), 1.0)
    assert np.linalg.eigvalsh(r).min() > 0  # positive definite


def test_default_correlation_is_within_player():
    _, default = params.correlation("WR")
    _, within = params.correlation("WR", "within")
    np.testing.assert_array_equal(default, within)


def test_unknown_kind_and_position_are_rejected():
    with pytest.raises(ValueError, match="kind"):
        params.correlation("WR", "tiered")
    with pytest.raises(ValueError, match="QB"):
        params.correlation("QB")


def test_every_params_file_records_its_source():
    for name in ("dispersion", "correlation", "base_rates"):
        assert params.load(name)["source"]
