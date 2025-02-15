"""
The setting of Superb ASV

Authors
  * Po-Han Chi 2021
  * Shu-wen Yang 2021
  * Haibin Wu 2022
  * Shu-wen Yang 2022
"""

import pickle
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from omegaconf import MISSING

from s3prl.dataio.corpus.voxceleb1sv import VoxCeleb1SV
from s3prl.dataset.common_pipes import LoadAudio, RandomCrop
from s3prl.dataio.encoder.category import CategoryEncoder
from s3prl.nn.speaker_model import SuperbXvector
from s3prl.dataio.sampler import FixedBatchSizeBatchSampler
from s3prl.util.download import _download

from .run import ASV

SAMPLE_RATE = 16000
EFFECTS = [
    ["channels", "1"],
    ["rate", "16000"],
    ["gain", "-3.0"],
    ["silence", "1", "0.1", "0.1%", "-1", "0.1", "0.1%"],
]

__all__ = [
    "prepare_voxceleb1_for_sv",
    "SuperbASV",
]


def prepare_voxceleb1_for_sv(
    target_dir: str,
    cache_dir: str,
    get_path_only: str,
    dataset_root: str,
    force_download: bool = False,
):
    """
    Prepare VoxCeleb1 for speaker verification
    following :obj:`SuperbASV.prepare_data` format.

    Args:
        dataset_root (str): The root path of Fluent Speech Command
        force_download (bool): always re-download the metadata for VoxCeleb1
    """

    train_path = target_dir / "train.csv"
    test_trial_path = target_dir / "test_trial.csv"

    if get_path_only:
        return train_path, [test_trial_path]

    corpus = VoxCeleb1SV(dataset_root, cache_dir, force_download)
    train_data, valid_data, test_data, test_trials = corpus.all_data
    all_data = {**train_data, **valid_data}

    ignored_utts_path = Path(cache_dir) / "voxceleb1_too_short_utts"
    _download(
        ignored_utts_path,
        "https://huggingface.co/datasets/s3prl/voxceleb1_too_short_utts/raw/main/utt",
        True,
    )
    with open(ignored_utts_path) as file:
        ignored_utts = [line.strip() for line in file.readlines()]

    for utt in ignored_utts:
        assert utt in all_data
        del all_data[utt]

    ids = sorted(all_data.keys())
    wav_paths = [all_data[idx]["wav_path"] for idx in ids]
    labels = [all_data[idx]["label"] for idx in ids]
    pd.DataFrame({"id": ids, "wav_path": wav_paths, "spk": labels}).to_csv(
        train_path, index=False
    )

    labels, id1s, id2s = zip(*test_trials)
    wav_path1 = [test_data[idx]["wav_path"] for idx in id1s]
    wav_path2 = [test_data[idx]["wav_path"] for idx in id2s]
    pd.DataFrame(
        {
            "id1": id1s,
            "id2": id2s,
            "wav_path1": wav_path1,
            "wav_path2": wav_path2,
            "label": labels,
        }
    ).to_csv(test_trial_path, index=False)

    return train_path, [test_trial_path]


class SuperbASV(ASV):
    def default_config(self):
        return dict(
            target_dir=MISSING,
            cache_dir=None,
            test_ckpt_steps=[
                20000,
                40000,
                60000,
                80000,
                100000,
                120000,
                140000,
                160000,
                180000,
                200000,
            ],
            prepare_data=dict(
                dataset_root=MISSING,
            ),
            build_dataset=dict(
                train=dict(
                    max_secs=8.0,
                ),
            ),
            build_batch_sampler=dict(
                train=dict(
                    batch_size=10,
                    shuffle=True,
                ),
                test=dict(
                    batch_size=1,
                ),
            ),
            build_upstream=dict(
                name="fbank",
            ),
            build_featurizer=dict(
                layer_selections=None,
                normalize=False,
            ),
            build_model=dict(
                upstream_trainable=False,
            ),
            build_task=dict(
                loss_type="amsoftmax",
                loss_cfg=dict(
                    margin=0.4,
                    scale=30,
                ),
            ),
            build_optimizer=dict(
                name="AdamW",
                conf=dict(
                    lr=1.0e-4,
                ),
            ),
            build_scheduler=dict(
                name="ExponentialLR",
                gamma=0.9,
            ),
            train=dict(
                total_steps=200000,
                log_step=500,
                eval_step=1e20,
                save_step=20000,
                gradient_clipping=1.0e3,
                gradient_accumulate=5,
                valid_metric=None,
                valid_higher_better=None,
                auto_resume=True,
                resume_ckpt_dir=None,
                keep_num_ckpts=10,
            ),
        )

    def prepare_data(
        self, prepare_data: dict, target_dir: str, cache_dir: str, get_path_only: bool
    ):
        """
        Prepare the task-specific data metadata (path, labels...).
        By default call :obj:`prepare_voxceleb1_for_sv` with :code:`**prepare_data`

        Args:
            prepare_data (dict): same in :obj:`default_config`,
                support arguments in :obj:`prepare_voxceleb1_for_sv`
            target_dir (str): Parse your corpus and save the csv file into this directory
            cache_dir (str): If the parsing or preprocessing takes too long time, you can save
                the temporary files into this directory. This directory is expected to be shared
                across different training sessions (different hypers and :code:`target_dir`)
            get_path_only (bool): Directly return the filepaths no matter they exist or not.

        Returns:
            tuple

            1. train_path (str)
            2. test_trial_paths (List[str])

            The :code:`train_path` should be a csv file containing the following columns:

            ====================  ====================
            column                description
            ====================  ====================
            id                    (str) - the unique id for this utterance
            wav_path              (str) - the absolute path of the waveform file
            spk                   (str) - a string speaker label
            ====================  ====================

            Each :code:`test_trial_path` should be a csv file containing the following columns:

            ====================  ====================
            column                description
            ====================  ====================
            id1                   (str) - the unique id of the first utterance
            id2                   (str) - the unique id of the second utterance
            wav_path1             (str) - the absolute path of the first utterance
            wav_path2             (str) - the absolute path of the second utterance
            label                 (int) - 0 when two utterances are from different speakers, \
                                    1 when same speaker
            ====================  ====================
        """
        return prepare_voxceleb1_for_sv(
            **self._get_current_arguments(flatten_dict="prepare_data")
        )

    def build_encoder(
        self,
        build_encoder: dict,
        target_dir: str,
        cache_dir: str,
        train_csv: str,
        test_csvs: list,
        get_path_only: bool,
    ):
        """
        Build the encoder (for the labels) given the data metadata, and return the saved encoder path.
        By default generate and save a :obj:`s3prl.dataio.encoder.CategoryEncoder` from the :code:`label` column of the train csv.

        Args:
            build_encoder (dict): same in :obj:`default_config`, no argument supported for now
            target_dir (str): Save your encoder into this directory
            cache_dir (str): If the preprocessing takes too long time, you can save
                the temporary files into this directory. This directory is expected to be shared
                across different training sessions (different hypers and :code:`target_dir`)
            train_csv_path (str): the train path from :obj:`prepare_data`
            valid_csv_path (str): the valid path from :obj:`prepare_data`
            test_csv_paths (List[str]): the test paths from :obj:`prepare_data`
            get_path_only (bool): Directly return the filepaths no matter they exist or not

        Returns:
            str

            encoder_path: The encoder should be saved in the pickle format
        """
        encoder_path = Path(target_dir) / "spk2int.pkl"
        if get_path_only:
            return encoder_path

        csv = pd.read_csv(train_csv)
        all_spk = sorted(set(csv["spk"]))
        spk2int = CategoryEncoder(all_spk)

        with open(encoder_path, "wb") as f:
            pickle.dump(spk2int, f)

        return encoder_path

    def build_dataset(
        self,
        build_dataset: dict,
        target_dir: str,
        cache_dir: str,
        mode: str,
        data_csv: str,
        encoder_path: str,
    ):
        """
        Build the dataset for train/valid/test.

        Args:
            build_dataset (dict): same in :obj:`default_config`, have
                :code:`train` and :code:`test` keys, each is a dictionary, for both dictionaries:

                ====================  ====================
                key                   description
                ====================  ====================
                max_secs              (float) - If a waveform is longer than :code:`max_secs` seconds, \
                                        randomly crop the waveform into :code:`max_secs` seconds. \
                                        Default: None, no cropping
                ====================  ====================

            target_dir (str): Current experiment directory
            cache_dir (str): If the preprocessing takes too long time, you can save
                the temporary files into this directory. This directory is expected to be shared
                across different training sessions (different hypers and :code:`target_dir`)
            mode (str): train/valid/test
            data_csv (str): The metadata csv file for the specific :code:`mode`
            encoder_path (str): The pickled encoder path for encoding the labels

        Returns:
            torch Dataset

            For all train/test mode, the dataset should return each item as a dictionary
            containing the following keys:

            ====================  ====================
            key                   description
            ====================  ====================
            x                     (torch.FloatTensor) - the waveform in (seq_len, 1)
            x_len                 (int) - the waveform length :code:`seq_len`
            label                 (str) - the class name
            unique_name           (str) - the unique id for this datapoint
            ====================  ====================
        """
        assert mode in [
            "train",
            "test",
        ], "Only support train & test mode (no validation)"

        if mode == "train":
            csv = pd.read_csv(data_csv)
            data = OrderedDict()
            for rowid, row in csv.iterrows():
                data[row["id"]] = dict(
                    wav_path=row["wav_path"],
                    label=row["spk"],
                )
            config = build_dataset.get("train", {})

        elif mode == "test":
            csv = pd.read_csv(data_csv)
            ids = pd.concat([csv["id1"], csv["id2"]], ignore_index=True).tolist()
            wav_paths = pd.concat(
                [csv["wav_path1"], csv["wav_path2"]], ignore_index=True
            ).tolist()
            data_list = sorted(set([(idx, path) for idx, path in zip(ids, wav_paths)]))
            data = OrderedDict()
            for idx, path in data_list:
                data[idx] = dict(
                    wav_path=path,
                    label=None,
                )
            config = build_dataset.get("test", {})

        output_keys = dict(
            x="wav",
            x_len="wav_len",
            label="label",
            unique_name="id",
        )

        # TODO: should try to remove this dependency
        from speechbrain.dataio.dataset import DynamicItemDataset

        dataset: DynamicItemDataset = LoadAudio(
            audio_sample_rate=SAMPLE_RATE, sox_effects=EFFECTS
        )(data)
        dataset.set_output_keys(output_keys)

        @dataclass
        class Config:
            max_secs: float = None

        config = Config(**config)

        if config.max_secs is not None:
            assert isinstance(config.max_secs, float)
            dataset = RandomCrop(sample_rate=SAMPLE_RATE, max_secs=config.max_secs)(
                dataset
            )
            output_keys["x"] = "wav_crop"
            output_keys["x_len"] = "wav_crop_len"

        dataset.set_output_keys(output_keys)
        return dataset

    def build_batch_sampler(
        self,
        build_batch_sampler: dict,
        target_dir: str,
        cache_dir: str,
        mode: str,
        data_csv: str,
        dataset,
    ):
        """
        Return the batch sampler for torch DataLoader.

        Args:
            build_batch_sampler (dict): same in :obj:`default_config`

                ====================  ====================
                key                   description
                ====================  ====================
                train                 (dict) - arguments for :obj:`FixedBatchSizeBatchSampler`
                test                  (dict) - arguments for :obj:`FixedBatchSizeBatchSampler`
                ====================  ====================

                Note that ASV does not support valid

            target_dir (str): Current experiment directory
            cache_dir (str): If the preprocessing takes too long time, save
                the temporary files into this directory. This directory is expected to be shared
                across different training sessions (different hypers and :code:`target_dir`)
            mode (str): train/valid/test
            data_csv (str): the :code:`mode` specific csv from :obj:`prepare_data`
            dataset: the dataset from :obj:`build_dataset`

        Returns:
            batch sampler for torch DataLoader
        """

        train = build_batch_sampler.get("train", {})
        test = build_batch_sampler.get("test", {})

        if mode == "train":
            return FixedBatchSizeBatchSampler(dataset, **train)
        elif mode == "test":
            return FixedBatchSizeBatchSampler(dataset, **test)
        else:
            raise ValueError("ASV only supports train/test modes")

    def build_downstream(
        self,
        build_downstream: dict,
        downstream_input_size: int,
        downstream_output_size: int,
        downstream_input_stride: int,
    ):
        """
        Return the task-specific downstream model.
        By default build the :obj:`SuperbXvector` model

        Args:
            build_downstream (dict): same in :obj:`default_config`, support arguments of :obj:`SuperbXvector`
            downstream_input_size (int): the required input size of the model
            downstream_output_size (int): the required output size of the model
            downstream_input_stride (int): the input feature's stride (from 16 KHz)

        Returns:
            :obj:`s3prl.nn.interface.AbsUtteranceModel`
        """
        return SuperbXvector(
            downstream_input_size, downstream_output_size, **build_downstream
        )
