import inspect
import os
import tempfile
import unittest

import numpy as np
import pandas as pd
import ray
import xgboost as xgb

try:
    import ray.data as ray_data
except (ImportError, ModuleNotFoundError):

    ray_data = None

from xgboost_ray import RayDMatrix
from xgboost_ray.matrix import RayShardingMode, _get_sharding_indices, concat_dataframes


class XGBoostRayDMatrixTest(unittest.TestCase):
    """This test suite validates core RayDMatrix functionality."""

    def setUp(self):
        repeat = 8  # Repeat data a couple of times for stability
        self.x = np.array(
            [
                [1, 0, 0, 0],  # Feature 0 -> Label 0
                [0, 1, 0, 0],  # Feature 1 -> Label 1
                [0, 0, 1, 1],  # Feature 2+3 -> Label 2
                [0, 0, 1, 0],  # Feature 2+!3 -> Label 3
            ]
            * repeat
        )
        self.y = np.array([0, 1, 2, 3] * repeat)
        self.multi_y = np.array(
            [
                [1, 0, 0, 0],
                [0, 1, 0, 0],
                [0, 0, 1, 1],
                [0, 0, 1, 0],
            ]
            * repeat
        )

    @classmethod
    def setUpClass(cls):
        ray.init()

    @classmethod
    def tearDownClass(cls):
        ray.shutdown()

    def testSameObject(self):
        """Test that matrices are recognized as the same in an actor task."""

        @ray.remote
        def same(one, two):
            return one == two

        data = RayDMatrix(self.x, self.y)
        self.assertTrue(ray.get(same.remote(data, data)))

    def testColumnOrdering(self):
        """When excluding cols, the remaining col order should be preserved."""

        cols = [str(i) for i in range(50)]
        df = pd.DataFrame(np.random.randn(1, len(cols)), columns=cols)
        matrix = RayDMatrix(df, label=cols[-1], num_actors=1)
        data = matrix.get_data(0)["data"]

        assert data.columns.tolist() == cols[:-1]

    def _testMatrixCreation(self, in_x, in_y, multi_label = False, **kwargs):
        if "sharding" not in kwargs:
            kwargs["sharding"] = RayShardingMode.BATCH
        mat = RayDMatrix(in_x, in_y, **kwargs)

        def _load_data(params):
            x = params["data"]
            y = params["label"]

            if isinstance(x, list):
                x = concat_dataframes(x)
            if isinstance(y, list):
                y = concat_dataframes(y)
            return x, y

        params = mat.get_data(rank=0, num_actors=1)
        x, y = _load_data(params)

        self.assertTrue(np.allclose(self.x, x))
        if multi_label:
            self.assertTrue(np.allclose(self.multi_y, y))
        else:
            self.assertTrue(np.allclose(self.y, y))

        # Multi actor check
        mat = RayDMatrix(in_x, in_y, **kwargs)

        params = mat.get_data(rank=0, num_actors=2)
        x1, y1 = _load_data(params)

        mat.unload_data()

        params = mat.get_data(rank=1, num_actors=2)
        x2, y2 = _load_data(params)

        self.assertTrue(np.allclose(self.x, concat_dataframes([x1, x2])))
        if multi_label:
            self.assertTrue(np.allclose(self.multi_y, concat_dataframes([y1, y2])))
        else:
            self.assertTrue(np.allclose(self.y, concat_dataframes([y1, y2])))

    def testFromNumpy(self):
        in_x = self.x
        in_y = self.y
        self._testMatrixCreation(in_x, in_y)

    def testFromPandasDfDf(self):
        in_x = pd.DataFrame(self.x)
        in_y = pd.DataFrame(self.y)
        self._testMatrixCreation(in_x, in_y)

    def testFromPandasDfSeries(self):
        in_x = pd.DataFrame(self.x)
        in_y = pd.Series(self.y)
        self._testMatrixCreation(in_x, in_y)

    def testFromPandasDfString(self):
        in_df = pd.DataFrame(self.x)
        in_df["label"] = self.y
        self._testMatrixCreation(in_df, "label")

    def testFromModinDfDf(self):
        from xgboost_ray.data_sources.modin import MODIN_INSTALLED

        if not MODIN_INSTALLED:
            self.skipTest("Modin not installed.")
            return

        from modin.pandas import DataFrame

        in_x = DataFrame(self.x)
        in_y = DataFrame(self.y)
        self._testMatrixCreation(in_x, in_y, distributed=False)

    def testFromModinDfSeries(self):
        from xgboost_ray.data_sources.modin import MODIN_INSTALLED

        if not MODIN_INSTALLED:
            self.skipTest("Modin not installed.")
            return

        from modin.pandas import DataFrame, Series

        in_x = DataFrame(self.x)
        in_y = Series(self.y)
        self._testMatrixCreation(in_x, in_y, distributed=False)

    def testFromModinDfString(self):
        from xgboost_ray.data_sources.modin import MODIN_INSTALLED

        if not MODIN_INSTALLED:
            self.skipTest("Modin not installed.")
            return

        from modin.pandas import DataFrame

        in_df = DataFrame(self.x)
        in_df["label"] = self.y
        self._testMatrixCreation(in_df, "label", distributed=False)
        self._testMatrixCreation(in_df, "label", distributed=True)

    def testFromDaskDfSeries(self):
        from xgboost_ray.data_sources.dask import DASK_INSTALLED

        if not DASK_INSTALLED:
            self.skipTest("Dask not installed.")
            return

        import dask.dataframe as dd

        in_x = dd.from_array(self.x)
        in_y = dd.from_array(self.y)

        self._testMatrixCreation(in_x, in_y, distributed=False)

    def testFromDaskDfArray(self):
        from xgboost_ray.data_sources.dask import DASK_INSTALLED

        if not DASK_INSTALLED:
            self.skipTest("Dask not installed.")
            return

        import dask.array as da
        import dask.dataframe as dd

        in_x = dd.from_array(self.x)
        in_y = da.from_array(self.y)

        self._testMatrixCreation(in_x, in_y, distributed=False)

    def testFromDaskDfString(self):
        from xgboost_ray.data_sources.dask import DASK_INSTALLED

        if not DASK_INSTALLED:
            self.skipTest("Dask not installed.")
            return

        import dask.dataframe as dd

        in_df = dd.from_array(self.x)
        in_df["label"] = dd.from_array(self.y)

        self._testMatrixCreation(in_df, "label", distributed=False)
        self._testMatrixCreation(in_df, "label", distributed=True)

    def testFromPetastormParquetString(self):
        try:
            import petastorm  # noqa: F401
        except ImportError:
            self.skipTest("Petastorm not installed.")
            return

        with tempfile.TemporaryDirectory() as dir:
            data_file = os.path.join(dir, "data.parquet")

            data_df = pd.DataFrame(self.x, columns=["a", "b", "c", "d"])
            data_df["label"] = pd.Series(self.y)
            data_df.to_parquet(data_file)

            self._testMatrixCreation(f"file://{data_file}", "label", distributed=False)
            self._testMatrixCreation(f"file://{data_file}", "label", distributed=True)

    def testFromPetastormMultiParquetString(self):
        with tempfile.TemporaryDirectory() as dir:
            data_file_1 = os.path.join(dir, "data_1.parquet")
            data_file_2 = os.path.join(dir, "data_2.parquet")

            data_df = pd.DataFrame(self.x, columns=["a", "b", "c", "d"])
            data_df["label"] = pd.Series(self.y)

            df_1 = data_df[0 : len(data_df) // 2]
            df_2 = data_df[len(data_df) // 2 :]

            df_1.to_parquet(data_file_1)
            df_2.to_parquet(data_file_2)

            self._testMatrixCreation(
                [f"file://{data_file_1}", f"file://{data_file_2}"],
                "label",
                distributed=False,
            )
            self._testMatrixCreation(
                [f"file://{data_file_1}", f"file://{data_file_2}"],
                "label",
                distributed=True,
            )

    def testFromCSVString(self):
        with tempfile.TemporaryDirectory() as dir:
            data_file = os.path.join(dir, "data.csv")

            data_df = pd.DataFrame(self.x, columns=["a", "b", "c", "d"])
            data_df["label"] = pd.Series(self.y)
            data_df.to_csv(data_file, header=True, index=False)

            self._testMatrixCreation(data_file, "label", distributed=False)
            with self.assertRaises(ValueError):
                self._testMatrixCreation(data_file, "label", distributed=True)

    def testFromMultiCSVString(self):
        with tempfile.TemporaryDirectory() as dir:
            data_file_1 = os.path.join(dir, "data_1.csv")
            data_file_2 = os.path.join(dir, "data_2.csv")

            data_df = pd.DataFrame(self.x, columns=["a", "b", "c", "d"])
            data_df["label"] = pd.Series(self.y)

            df_1 = data_df[0 : len(data_df) // 2]
            df_2 = data_df[len(data_df) // 2 :]

            df_1.to_csv(data_file_1, header=True, index=False)
            df_2.to_csv(data_file_2, header=True, index=False)

            self._testMatrixCreation(
                [data_file_1, data_file_2], "label", distributed=False
            )
            self._testMatrixCreation(
                [data_file_1, data_file_2], "label", distributed=True
            )

    def testFromParquetStringMultiLabel(self):
        with tempfile.TemporaryDirectory() as dir:
            data_file = os.path.join(dir, "data.parquet")

            data_df = pd.DataFrame(self.x, columns=["a", "b", "c", "d"])
            labels = [f"label_{label}" for label in range(4)]
            data_df[labels] = self.multi_y
            data_df.to_parquet(data_file)

            self._testMatrixCreation(data_file, labels, multi_label=True, distributed=False)
            self._testMatrixCreation(data_file, labels, multi_label=True, distributed=True)

    def testFromParquetString(self):
        with tempfile.TemporaryDirectory() as dir:
            data_file = os.path.join(dir, "data.parquet")

            data_df = pd.DataFrame(self.x, columns=["a", "b", "c", "d"])
            data_df["label"] = pd.Series(self.y)
            data_df.to_parquet(data_file)

            self._testMatrixCreation(data_file, "label", distributed=False)
            self._testMatrixCreation(data_file, "label", distributed=True)
    
    def testFromMultiParquetStringMultiLabel(self):
        with tempfile.TemporaryDirectory() as dir:
            data_file_1 = os.path.join(dir, "data_1.parquet")
            data_file_2 = os.path.join(dir, "data_2.parquet")

            data_df = pd.DataFrame(self.x, columns=["a", "b", "c", "d"])
            labels = [f"label_{label}" for label in range(4)]
            data_df[labels] = self.multi_y

            df_1 = data_df[0 : len(data_df) // 2]
            df_2 = data_df[len(data_df) // 2 :]

            df_1.to_parquet(data_file_1)
            df_2.to_parquet(data_file_2)

            self._testMatrixCreation(
                [data_file_1, data_file_2], labels, multi_label=True, distributed=False
            )
            self._testMatrixCreation(
                [data_file_1, data_file_2], labels, multi_label=True, distributed=True
            )

    def testFromMultiParquetString(self):
        with tempfile.TemporaryDirectory() as dir:
            data_file_1 = os.path.join(dir, "data_1.parquet")
            data_file_2 = os.path.join(dir, "data_2.parquet")

            data_df = pd.DataFrame(self.x, columns=["a", "b", "c", "d"])
            data_df["label"] = pd.Series(self.y)

            df_1 = data_df[0 : len(data_df) // 2]
            df_2 = data_df[len(data_df) // 2 :]

            df_1.to_parquet(data_file_1)
            df_2.to_parquet(data_file_2)

            self._testMatrixCreation(
                [data_file_1, data_file_2], "label", distributed=False
            )
            self._testMatrixCreation(
                [data_file_1, data_file_2], "label", distributed=True
            )

    def testDetectDistributed(self):
        with tempfile.TemporaryDirectory() as dir:
            parquet_file = os.path.join(dir, "file.parquet")
            csv_file = os.path.join(dir, "file.csv")

            data_df = pd.DataFrame(self.x, columns=["a", "b", "c", "d"])
            data_df["label"] = pd.Series(self.y)

            data_df.to_parquet(parquet_file)
            data_df.to_csv(csv_file)

            mat = RayDMatrix(parquet_file, lazy=True)
            self.assertTrue(mat.distributed)

            mat = RayDMatrix(csv_file, lazy=True)
            # Single CSV files should not be distributed
            self.assertFalse(mat.distributed)

            mat = RayDMatrix([parquet_file] * 3, lazy=True)
            self.assertTrue(mat.distributed)

            mat = RayDMatrix([csv_file] * 3, lazy=True)
            self.assertTrue(mat.distributed)

            if ray_data:
                ds = ray_data.read_parquet(parquet_file)
                mat = RayDMatrix(ds)
                self.assertTrue(mat.distributed)

    def testTooManyActorsDistributed(self):
        """Test error when too many actors are passed"""
        with self.assertRaises(RuntimeError):
            dtrain = RayDMatrix(["foo.csv"], num_actors=4, distributed=True)
            dtrain.assert_enough_shards_for_actors(4)

    def testTooManyActorsCentral(self):
        """Test error when too many actors are passed"""
        data_df = pd.DataFrame(self.x, columns=["a", "b", "c", "d"])

        with self.assertRaises(RuntimeError):
            RayDMatrix(data_df, num_actors=34, distributed=False)

    def testBatchShardingAllActorsGetIndices(self):
        """Check if all actors get indices with batch mode"""
        for i in range(16):
            self.assertTrue(_get_sharding_indices(RayShardingMode.BATCH, i, 16, 100))

    def testLegacyParams(self):
        """Test if all params can be set regardless of xgb version"""
        in_x = self.x
        in_y = self.y
        weight = np.array([1] * len(in_y))
        qid = np.array([0] + [1] * len(in_y - 1))
        base_margin = np.array([1] * len(in_y))
        label_lower_bound = np.array([0.1] * len(in_y))
        label_upper_bound = np.array([1] * len(in_y))
        self._testMatrixCreation(
            in_x,
            in_y,
            weight=weight,
            base_margin=base_margin,
            label_lower_bound=label_lower_bound,
            label_upper_bound=label_upper_bound,
        )
        self._testMatrixCreation(
            in_x,
            in_y,
            qid=qid,
            base_margin=base_margin,
            label_lower_bound=label_lower_bound,
            label_upper_bound=label_upper_bound,
        )

    @unittest.skipIf(
        xgb.__version__ < "1.3.0", f"not supported in xgb version {xgb.__version__}"
    )
    def testFeatureWeightsParam(self):
        """Test the feature_weights parameter for xgb version >= 1.3.0"""
        in_x = self.x
        in_y = self.y
        feature_weights = np.arange(len(in_y))
        self._testMatrixCreation(in_x, in_y, feature_weights=feature_weights)

    @unittest.skipIf(
        "qid" not in inspect.signature(xgb.DMatrix).parameters,
        f"not supported in xgb version {xgb.__version__}",
    )
    def testQidSortedBehaviorXGBoost(self):
        """Test that data with unsorted qid is sorted in RayDMatrix"""
        in_x = self.x
        in_y = self.y
        unsorted_qid = np.array([1, 2] * 16)

        from xgboost import DMatrix

        with self.assertRaises(ValueError):
            DMatrix(**{"data": in_x, "label": in_y, "qid": unsorted_qid})
        DMatrix(
            **{"data": in_x, "label": in_y, "qid": np.sort(unsorted_qid)}
        )  # no exception
        # test RayDMatrix handles sorting automatically
        mat = RayDMatrix(in_x, in_y, qid=unsorted_qid)
        params = mat.get_data(rank=0, num_actors=1)
        DMatrix(**params)

    @unittest.skipIf(
        "qid" not in inspect.signature(xgb.DMatrix).parameters,
        f"not supported in xgb version {xgb.__version__}",
    )
    def testQidSortedParquet(self):
        from xgboost import DMatrix

        with tempfile.TemporaryDirectory() as dir:
            parquet_file1 = os.path.join(dir, "file1.parquet")
            parquet_file2 = os.path.join(dir, "file2.parquet")

            unsorted_qid1 = np.array([2, 4] * 16)
            unsorted_qid2 = np.array([1, 3] * 16)

            # parquet 1
            data_df = pd.DataFrame(self.x, columns=["a", "b", "c", "d"])
            data_df["label"] = pd.Series(self.y)
            data_df["group"] = pd.Series(unsorted_qid1)
            data_df.to_parquet(parquet_file1)
            # parquet 2
            data_df = pd.DataFrame(self.x, columns=["a", "b", "c", "d"])
            data_df["label"] = pd.Series(self.y)
            data_df["group"] = pd.Series(unsorted_qid2)
            data_df.to_parquet(parquet_file2)
            mat = RayDMatrix(
                [parquet_file1, parquet_file2],
                columns=["a", "b", "c", "d", "label", "group"],
                label="label",
                qid="group",
            )
            params = mat.get_data(rank=0, num_actors=1)
            DMatrix(**params)


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main(["-v", __file__]))
