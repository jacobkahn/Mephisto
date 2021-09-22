#!/usr/bin/env python3

# Copyright (c) Facebook, Inc. and its affiliates.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

from typing import (
    Optional,
    Dict,
    Any,
    Union,
    Iterable,
    Callable,
    Tuple,
    Generator,
    TYPE_CHECKING,
)

import types
from mephisto.abstractions.blueprint import BlueprintMixin
from dataclasses import dataclass, field
from omegaconf import MISSING, DictConfig
from mephisto.data_model.qualification import make_qualification_dict, QUAL_NOT_EXIST
from mephisto.operations.utils import find_or_create_qualification


if TYPE_CHECKING:
    from mephisto.data_model.task_run import TaskRun
    from mephisto.data_model.unit import Unit
    from mephisto.data_model.packet import Packet
    from mephisto.data_model.worker import Worker
    from argparse import _ArgumentGroup as ArgumentGroup


@dataclass
class ScreenTaskRequiredArgs:
    passed_qualification_name: str = field(
        default=MISSING,
        metadata={
            "help": (
                "Specify the name of a qualification used to designate "
                "workers who have passed screening."
            )
        },
    )
    max_screening_units: int = field(
        default=MISSING,
        metadata={
            "help": (
                "The maximum number of screening units that can be launched "
                "with this batch, specified to limit the number of validations "
                "you may need to pay out for."
            )
        },
    )
    use_screening_task: bool = field(
        default=MISSING,
        metadata={"help": ("Whether or not to use a screening task in this run.")},
    )


ScreenUnitDataGenerator = Generator[Dict[str, Any], None, None]


@dataclass
class ScreenTaskSharedState:
    onboarding_data: Dict[str, Any] = field(default_factory=dict)
    generate_screening_unit_data: Tuple[bool, ScreenUnitDataGenerator] = field(
        default_factory=lambda: (lambda x: {})
    )


class ScreenTaskRequired(BlueprintMixin):
    """
    Compositional class for blueprints that may have a first task to
    qualify workers who have never attempted the task before
    """

    def init_mixin_config(
        self,
        task_run: "TaskRun",
        args: "DictConfig",
        shared_state: "ScreenTaskSharedState",
    ) -> None:
        return self.init_screening_config(task_run, args, shared_state)

    def init_screening_config(
        self,
        task_run: "TaskRun",
        args: "DictConfig",
        shared_state: "ScreenTaskSharedState",
    ) -> None:
        self.use_screening_task = args.blueprint.get("use_screening_task", False)
        if not self.use_screening_task:
            return

        # Runs that are using a qualification task should be able to assign
        # a specially generated unit to unqualified workers
        self.passed_qualification_name = args.blueprint.passed_qualification_name
        self.failed_qualification_name = args.blueprint.block_qualification
        self.generate_screening_unit_data: Tuple[
            bool, ScreenUnitDataGenerator
        ] = shared_state.generate_screening_unit_data
        self.screening_units_launched = 0
        self.screening_unit_cap = args.blueprint.max_screening_units

        find_or_create_qualification(task_run.db, self.passed_qualification_name)
        find_or_create_qualification(task_run.db, self.failed_qualification_name)

    @classmethod
    def assert_task_args(cls, args: "DictConfig", shared_state: "SharedTaskState"):
        use_screening_task = args.blueprint.get("use_screening_task", False)
        if not use_screening_task:
            return
        passed_qualification_name = args.blueprint.passed_qualification_name
        failed_qualification_name = args.blueprint.block_qualification
        assert args.task.allowed_concurrent == 1, (
            "Can only run this task type with one allowed task at a time per worker, to ensure screening "
            "before moving into more tasks."
        )
        assert (
            passed_qualification_name is not None
        ), "Must supply an passed_qualification_name in Hydra args to use a qualification task"
        assert (
            failed_qualification_name is not None
        ), "Must supply an block_qualification in Hydra args to use a qualification task"
        assert hasattr(shared_state, "generate_screening_unit_data"), (
            "You must supply a generate_screening_unit_data function in your SharedTaskState to use "
            "qualification tasks."
        )
        max_screening_units = args.blueprint.max_screening_units
        assert max_screening_units is not None, (
            "You must supply a blueprint.max_screening_units argument to set the maximum number of "
            "additional tasks you will pay out for the purpose of validating new workers. "
        )
        generate_screening_unit_data = shared_state.generate_screening_unit_data
        if generate_screening_unit_data is not False:
            assert isinstance(generate_screening_unit_data, types.GeneratorType), (
                "Must provide a generator function to SharedTaskState.generate_screening_unit_data if "
                "you want to generate screening tasks on the fly, or False if you can validate on any task "
            )

    def worker_needs_screening(self, worker: "Worker") -> bool:
        """Workers that are able to access the task (not blocked) but are not passed need qualification"""
        return worker.get_granted_qualification(self.passed_qualification_name) is None

    def should_generate_unit(self) -> bool:
        return self.generate_screening_unit_data is not False

    def get_screening_unit_data(self) -> Optional[Dict[str, Any]]:
        try:
            if self.screening_units_launched > self.screening_unit_cap:
                return None  # Exceeded the cap on these units
            else:
                return next(self.generate_screening_unit_data)
        except StopIteration:
            return None  # No screening units left...

    @classmethod
    def create_validation_function(
        cls, args: "DictConfig", screen_unit: Callable[["Unit"], bool]
    ):
        """
        Takes in a validator function to determine if validation units are
        passable, and returns a `on_unit_submitted` function to be used
        in the SharedTaskState
        """
        passed_qualification_name = args.blueprint.passed_qualification_name
        failed_qualification_name = args.blueprint.block_qualification

        def _wrapped_validate(unit):
            if unit.unit_index >= 0:
                return  # We only run validation on the validatable units

            validation_result = screen_unit(unit)
            agent = unit.get_assigned_agent()
            if validation_result is True:
                agent.get_worker().grant_qualification(passed_qualification_name)
            elif validation_result is False:
                agent.get_worker().grant_qualification(failed_qualification_name)

        return _wrapped_validate

    @classmethod
    def get_mixin_qualifications(cls, args: "DictConfig"):
        """Creates the relevant task qualifications for this task"""
        passed_qualification_name = args.blueprint.passed_qualification_name
        failed_qualification_name = args.blueprint.block_qualification
        return [
            make_qualification_dict(
                cls.get_failed_qual(failed_qualification_name),
                QUAL_NOT_EXIST,
                None,
            )
        ]
