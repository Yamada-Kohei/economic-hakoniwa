"""pytest の差し込み（-p wh_plugin）: 関門の試験の WAREHOUSE_TURNS・NEED_TURNS を環境変数で替える（測りだけ）。"""

import os


def pytest_runtest_setup(item):
    if "W272_WH_TURNS" in os.environ:
        item.module.WAREHOUSE_TURNS = float(os.environ["W272_WH_TURNS"])
    if "W272_NEED_TURNS" in os.environ:
        item.module.NEED_TURNS = int(os.environ["W272_NEED_TURNS"])
