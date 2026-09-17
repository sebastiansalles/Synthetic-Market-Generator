"""Scaffold smoke test — proves the package is importable and pytest collects."""


def test_package_imports():
    import synthetic

    assert synthetic.__name__ == "synthetic"
