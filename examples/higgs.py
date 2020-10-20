import os
import time

from xgboost_ray import train, RayDMatrix

import pandas as pd


def main():
    # Example adapted from this blog post:
    # https://medium.com/rapids-ai/a-new-official-dask-api-for-xgboost-e8b10f3d1eb7
    # This uses the HIGGS dataset. Download here:
    # https://archive.ics.uci.edu/ml/machine-learning-databases/00280/HIGGS.csv.gz
    fname = "HIGGS.csv"
    colnames = ["label"] + ["feature-%02d" % i for i in range(1, 29)]

    # data = pd.read_csv(fname, header=None, names=colnames)

    # dtrain = RayDMatrix(data, label="label")

    dtrain = RayDMatrix(os.path.abspath("HIGGS.parquet"), label="label", columns=colnames)

    config = {
        "tree_method": "hist",
        "eval_metric": ["logloss", "error"],
    }

    start = time.time()
    bst, evals = train(
                config,
                dtrain,
                num_boost_round=100,
                evals=[(dtrain, "train")])
    taken = time.time() - start
    print(f"TRAIN TIME TAKEN: {taken:.2f} seconds")

    bst.save_model('higgs.xgb')
    print("Final training error: {:.4f}".format(evals["train"]["error"][-1]))


if __name__ == "__main__":
    start = time.time()
    main()
    taken = time.time() - start
    print(f"TOTAL TIME TAKEN: {taken:.2f} seconds")
