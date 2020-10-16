import os

from typing import *
from cftool.misc import timestamp
from cftool.misc import update_dict
from cftool.misc import shallow_copy_dict
from cftool.misc import LoggingMixin
from cftool.ml.utils import pattern_type
from cftool.ml.utils import patterns_type
from cftool.ml.utils import Comparer
from cftool.ml.utils import Estimator
from cftool.ml.utils import ModelPattern
from cftool.ml.utils import EnsemblePattern
from cfdata.tabular import TaskTypes
from cfdata.tabular import TabularData
from cfdata.tabular import TimeSeriesConfig
from optuna.trial import Trial

from ..dist import *
from ..misc.toolkit import *
from ..pipeline.core import Pipeline


def make(
    model: str = "fcnn",
    *,
    delim: str = None,
    task_type: str = None,
    skip_first: bool = None,
    cv_split: Union[float, int] = 0.1,
    min_epoch: int = None,
    num_epoch: int = None,
    max_epoch: int = None,
    batch_size: int = None,
    max_snapshot_num: int = None,
    clip_norm: float = None,
    ema_decay: float = None,
    ts_config: TimeSeriesConfig = None,
    aggregation: str = None,
    aggregation_config: Dict[str, Any] = None,
    ts_label_collator_config: Dict[str, Any] = None,
    data_config: Dict[str, Any] = None,
    read_config: Dict[str, Any] = None,
    model_config: Dict[str, Any] = None,
    metrics: Union[str, List[str]] = None,
    metric_config: Dict[str, Any] = None,
    optimizer: str = None,
    scheduler: str = None,
    optimizer_config: Dict[str, Any] = None,
    scheduler_config: Dict[str, Any] = None,
    optimizers: Dict[str, Any] = None,
    logging_file: str = None,
    logging_folder: str = None,
    trigger_logging: bool = None,
    trial: Trial = None,
    tracker_config: Dict[str, Any] = None,
    cuda: Union[int, str] = None,
    verbose_level: int = 2,
    use_timing_context: bool = True,
    use_tqdm: bool = True,
    **kwargs: Any,
) -> Pipeline:
    # pipeline general
    kwargs["model"] = model
    kwargs["cv_split"] = cv_split
    kwargs["use_timing_context"] = use_timing_context
    if data_config is None:
        data_config = {}
    if ts_config is not None:
        data_config["time_series_config"] = ts_config
    if task_type is not None:
        data_config["task_type"] = TaskTypes.from_str(task_type)
    if read_config is None:
        read_config = {}
    if delim is not None:
        read_config["delim"] = delim
    if skip_first is not None:
        read_config["skip_first"] = skip_first
    kwargs["data_config"] = data_config
    kwargs["read_config"] = read_config
    if model_config is not None:
        kwargs["model_config"] = model_config
    if logging_folder is not None:
        if logging_file is None:
            logging_file = f"{model}_{timestamp()}.log"
        kwargs["logging_folder"] = logging_folder
        kwargs["logging_file"] = logging_file
    if trigger_logging is not None:
        kwargs["trigger_logging"] = trigger_logging
    # trainer general
    trainer_config = kwargs.setdefault("trainer_config", {})
    trainer_config["use_tqdm"] = use_tqdm
    if min_epoch is not None:
        trainer_config["min_epoch"] = min_epoch
    if num_epoch is not None:
        trainer_config["num_epoch"] = num_epoch
    if max_epoch is not None:
        trainer_config["max_epoch"] = max_epoch
    if batch_size is not None:
        trainer_config["batch_size"] = batch_size
    if max_snapshot_num is not None:
        trainer_config["max_snapshot_num"] = max_snapshot_num
    if clip_norm is not None:
        trainer_config["clip_norm"] = clip_norm
    if ema_decay is not None:
        trainer_config["ema_decay"] = ema_decay
    sampler_config = trainer_config.setdefault("sampler_config", {})
    if aggregation is not None:
        sampler_config["aggregation"] = aggregation
    if aggregation_config is not None:
        sampler_config["aggregation_config"] = aggregation_config
    if ts_label_collator_config is not None:
        trainer_config["ts_label_collator_config"] = ts_label_collator_config
    # metrics
    if metric_config is not None:
        if metrics is not None:
            print(
                f"{LoggingMixin.warning_prefix}`metrics` is set to '{metrics}' "
                f"but `metric_config` is provided, so `metrics` will be ignored"
            )
    elif metrics is not None:
        metric_config = {"types": metrics}
    if metric_config is not None:
        trainer_config["metric_config"] = metric_config
    # optimizers
    if optimizers is not None:
        if optimizer is not None:
            print(
                f"{LoggingMixin.warning_prefix}`optimizer` is set to '{optimizer}' "
                f"but `optimizers` is provided, so `optimizer` will be ignored"
            )
        if optimizer_config is not None:
            print(
                f"{LoggingMixin.warning_prefix}`optimizer_config` is set to '{optimizer_config}' "
                f"but `optimizers` is provided, so `optimizer_config` will be ignored"
            )
    else:
        preset_optimizer = {}
        if optimizer is not None:
            if optimizer_config is None:
                optimizer_config = {}
            preset_optimizer = {
                "optimizer": optimizer,
                "optimizer_config": optimizer_config,
            }
        if scheduler is not None:
            if scheduler_config is None:
                scheduler_config = {}
            preset_optimizer.update(
                {"scheduler": scheduler, "scheduler_config": scheduler_config}
            )
        if preset_optimizer:
            optimizers = {"all": preset_optimizer}
    if optimizers is not None:
        trainer_config["optimizers"] = optimizers
    return Pipeline(
        kwargs,
        cuda=cuda,
        trial=trial,
        tracker_config=tracker_config,
        verbose_level=verbose_level,
    )


SAVING_DELIM = "^_^"
pipelines_dict_type = Dict[str, Pipeline]
pipelines_type = Union[Pipeline, List[Pipeline], pipelines_dict_type]


def _to_saving_path(identifier: str, saving_folder: Optional[str]) -> str:
    if saving_folder is None:
        saving_path = identifier
    else:
        saving_path = os.path.join(saving_folder, identifier)
    return saving_path


def _make_saving_path(name: str, saving_path: str, remove_existing: bool) -> str:
    saving_path = os.path.abspath(saving_path)
    saving_folder, identifier = os.path.split(saving_path)
    postfix = f"{SAVING_DELIM}{name}"
    if os.path.isdir(saving_folder) and remove_existing:
        for existing_model in os.listdir(saving_folder):
            if os.path.isdir(os.path.join(saving_folder, existing_model)):
                continue
            if existing_model.startswith(f"{identifier}{postfix}"):
                print(
                    f"{LoggingMixin.warning_prefix}"
                    f"'{existing_model}' was found, it will be removed"
                )
                os.remove(os.path.join(saving_folder, existing_model))
    return f"{saving_path}{postfix}"


def _to_pipelines(pipelines: pipelines_type) -> pipelines_dict_type:
    if not isinstance(pipelines, dict):
        if not isinstance(pipelines, list):
            pipelines = [pipelines]
        names: List[str] = [
            pipeline.model.__identifier__ for pipeline in pipelines  # type: ignore
        ]
        if len(set(names)) != len(pipelines):
            raise ValueError(
                "pipeline names are not provided "
                "but identical pipeline.model is detected"
            )
        pipelines = dict(zip(names, pipelines))
    return pipelines


def estimate(
    x: data_type,
    y: data_type = None,
    *,
    contains_labels: bool = False,
    pipelines: pipelines_type = None,
    predict_config: Dict[str, Any] = None,
    metrics: Union[str, List[str]] = None,
    other_patterns: Dict[str, patterns_type] = None,
    comparer_verbose_level: Union[int, None] = 1,
) -> Comparer:
    patterns = {}
    if pipelines is None:
        if y is None:
            raise ValueError("either `pipelines` or `y` should be provided")
        if metrics is None:
            raise ValueError("either `pipelines` or `metrics` should be provided")
        if other_patterns is None:
            raise ValueError(
                "either `pipelines` or `other_patterns` should be provided"
            )
    else:
        pipelines = _to_pipelines(pipelines)
        if predict_config is None:
            predict_config = {}
        predict_config.setdefault("contains_labels", contains_labels)
        for name, pipeline in pipelines.items():
            if y is not None:
                y = to_2d(y)
            else:
                x, y = pipeline.tr_data.read_file(x, contains_labels=contains_labels)
                y = pipeline.tr_data.transform(x, y).y
            if metrics is None:
                metrics = [
                    k for k, v in pipeline.trainer.metrics.items() if v is not None
                ]
            # TODO : check whether this is handled correctly
            with eval_context(pipeline.model):
                patterns[name] = pipeline.to_pattern(**predict_config)
    if other_patterns is not None:
        for other_name in other_patterns.keys():
            if other_name in patterns:
                prefix = LoggingMixin.warning_prefix
                print(
                    f"{prefix}'{other_name}' is found in `other_patterns`, it will be overwritten"
                )
        update_dict(other_patterns, patterns)

    if isinstance(metrics, list):
        metrics_list = metrics
    else:
        assert isinstance(metrics, str)
        metrics_list = [metrics]

    estimators = list(map(Estimator, metrics_list))
    comparer = Comparer(patterns, estimators)
    comparer.compare(x, y, verbose_level=comparer_verbose_level)
    return comparer


def save(
    pipelines: pipelines_type,
    identifier: str = "cflearn",
    saving_folder: Optional[str] = None,
) -> pipelines_dict_type:
    pipelines = _to_pipelines(pipelines)
    saving_path = _to_saving_path(identifier, saving_folder)
    for name, pipeline in pipelines.items():
        pipeline.save(_make_saving_path(name, saving_path, True), compress=True)
    return pipelines


def _fetch_saving_paths(
    identifier: str = "cflearn",
    saving_folder: Optional[str] = None,
) -> Dict[str, str]:
    paths = {}
    saving_path = _to_saving_path(identifier, saving_folder)
    saving_path = os.path.abspath(saving_path)
    base_folder = os.path.dirname(saving_path)
    for existing_model in os.listdir(base_folder):
        if not os.path.isfile(os.path.join(base_folder, existing_model)):
            continue
        existing_model, existing_extension = os.path.splitext(existing_model)
        if existing_extension != ".zip":
            continue
        if SAVING_DELIM in existing_model:
            *folder, name = existing_model.split(SAVING_DELIM)
            if os.path.join(base_folder, SAVING_DELIM.join(folder)) != saving_path:
                continue
            paths[name] = _make_saving_path(name, saving_path, False)
    return paths


def load(
    identifier: str = "cflearn",
    saving_folder: str = None,
) -> pipelines_dict_type:
    paths = _fetch_saving_paths(identifier, saving_folder)
    pipelines = {k: Pipeline.load(v, compress=True) for k, v in paths.items()}
    if not pipelines:
        raise ValueError(
            f"'{identifier}' models not found with `saving_folder`={saving_folder}"
        )
    return pipelines


def _remove(identifier: str = "cflearn", saving_folder: str = None) -> None:
    for path in _fetch_saving_paths(identifier, saving_folder).values():
        path = f"{path}.zip"
        print(f"{LoggingMixin.info_prefix}removing {path}...")
        os.remove(path)


def load_task(task: Task) -> Pipeline:
    return next(iter(load(saving_folder=task.saving_folder).values()))


def transform_experiments(experiments: Experiments) -> Dict[str, List[Pipeline]]:
    return {k: list(map(load_task, v)) for k, v in experiments.tasks.items()}


class RepeatResult(NamedTuple):
    data: Optional[TabularData]
    experiments: Optional[Experiments]
    pipelines: Optional[Dict[str, List[Pipeline]]]
    patterns: Optional[Dict[str, List[ModelPattern]]]


def repeat_with(
    x: data_type,
    y: data_type = None,
    x_cv: data_type = None,
    y_cv: data_type = None,
    *,
    models: Union[str, List[str]] = "fcnn",
    identifiers: Union[str, List[str]] = None,
    num_jobs: int = 4,
    num_repeat: int = 5,
    temp_folder: str = "__tmp__",
    return_patterns: bool = True,
    pattern_kwargs: Dict[str, Any] = None,
    sequential: bool = None,
    use_tqdm: bool = True,
    **kwargs: Any,
) -> RepeatResult:

    if isinstance(models, str):
        models = [models]
    if identifiers is None:
        identifiers = models.copy()
    elif isinstance(identifiers, str):
        identifiers = [identifiers]

    kwargs.setdefault("trigger_logging", False)
    kwargs["verbose_level"] = 0

    if sequential is None:
        sequential = num_jobs <= 1

    pipelines_dict: Optional[Dict[str, List[Pipeline]]] = None
    if sequential:
        experiments = None
        kwargs["use_tqdm"] = False

        if not return_patterns:
            print(
                f"{LoggingMixin.warning_prefix}`return_patterns` should be True "
                "when `sequential` is True, because patterns will always be generated"
            )
            return_patterns = True

        def get(i_: int, model_: str) -> Pipeline:
            kwargs_ = shallow_copy_dict(kwargs)
            logging_folder = os.path.join(temp_folder, str(i_))
            m = make(model_, logging_folder=logging_folder, **kwargs_)
            return m.fit(x, y, x_cv, y_cv)

        pipelines_dict = {}
        for model, identifier in zip(models, identifiers):
            model_list = [model] * num_repeat
            pipelines_dict[identifier] = list(map(get, range(num_repeat), model_list))
    else:
        if num_jobs <= 1:
            print(
                f"{LoggingMixin.warning_prefix}we suggest setting `sequential` "
                f"to True when `num_jobs` is {num_jobs}"
            )

        experiments = Experiments(temp_folder, overwrite=False)
        experiments.run(
            None,
            x,
            y,
            x_cv,
            y_cv,
            models=models,
            identifiers=identifiers,
            num_repeat=num_repeat,
            num_jobs=num_jobs,
            use_tqdm=use_tqdm,
            temp_folder=temp_folder,
            **kwargs,
        )
        if return_patterns:
            pipelines_dict = transform_experiments(experiments)

    patterns = None
    if return_patterns:
        assert pipelines_dict is not None
        if pattern_kwargs is None:
            pattern_kwargs = {}
        patterns = {
            model: [m.to_pattern(**pattern_kwargs) for m in pipelines]
            for model, pipelines in pipelines_dict.items()
        }

    data = None
    if patterns is not None:
        data = patterns[identifiers[0]][0].model.tr_data

    return RepeatResult(data, experiments, pipelines_dict, patterns)


def tasks_to_pipelines(tasks: List[Task]) -> List[Pipeline]:
    return list(map(load_task, tasks))


def tasks_to_patterns(tasks: List[Task], **kwargs: Any) -> List[pattern_type]:
    pipelines = tasks_to_pipelines(tasks)
    return [m.to_pattern(**kwargs) for m in pipelines]


__all__ = [
    "make",
    "save",
    "load",
    "estimate",
    "load_task",
    "repeat_with",
    "tasks_to_pipelines",
    "tasks_to_patterns",
    "transform_experiments",
    "Task",
    "Experiments",
    "ModelPattern",
    "EnsemblePattern",
    "RepeatResult",
    "SAVING_DELIM",
    "_remove",
]
