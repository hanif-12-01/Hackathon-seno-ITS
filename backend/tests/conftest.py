import pytest


@pytest.fixture(autouse=True)
def prohibit_training(monkeypatch):
    from xgboost import XGBRegressor

    def forbidden(*args, **kwargs):
        raise AssertionError('Regression tests must use saved weights; training is forbidden')

    monkeypatch.setattr(XGBRegressor, 'fit', forbidden)
