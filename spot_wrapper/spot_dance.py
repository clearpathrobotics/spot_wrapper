import time
import tempfile
import os

from bosdyn.choreography.client.choreography import (
    load_choreography_sequence_from_txt_file,
    ChoreographyClient,
)
from bosdyn.client import ResponseError
from bosdyn.client.exceptions import UnauthenticatedError
from bosdyn.client.robot import Robot
from bosdyn.choreography.client.choreography import ChoreographyClient
from bosdyn.choreography.client.animation_file_to_proto import (
    convert_animation_file_to_proto,
)
from bosdyn.api.spot import choreography_sequence_pb2
from google.protobuf import text_format
from rclpy.impl.rcutils_logger import RcutilsLogger
from typing import Tuple, List


class CommandTimedOutError(Exception):
    """Raised when call to command does not return completion state in the stipulated time"""
    pass

class SpotDance:
    def __init__(
        self,
        robot: Robot,
        choreography_client: ChoreographyClient,
        logger: RcutilsLogger,
    ):
        self._robot = robot
        self._choreography_client = choreography_client
        self._logger = logger

    def upload_animation(
        self, animation_name: str, animation_file_content: str
    ) -> Tuple[bool, str]:
        """uploads an animation file"""
        # Load the animation file by saving the content to a temp file
        with tempfile.TemporaryDirectory() as temp_dir:
            filename = os.path.join(temp_dir, animation_name + ".cha")
            with open(filename, "w") as tmp:
                tmp.write(animation_file_content)
            try:
                animation_pb = convert_animation_file_to_proto(filename).proto
            except Exception as e:
                return (
                    False,
                    "Failed to convert animation file to protobuf message: {}".format(
                        e
                    ),
                )
            try:
                self._logger.info("Uploading the name {}".format(animation_name))
                upload_response = self._choreography_client.upload_animated_move(
                    animation_pb, animation_name
                )
            except Exception as e:
                error_msg = "Failed to upload animation: {}".format(e)
                return False, error_msg
        return True, "Success"

    def list_all_dances(self) -> Tuple[bool, str, List[str]]:
        """list all uploaded dances"""
        try:
            dances = self._choreography_client.list_all_sequences().sequence_info
            dances = [dance.name for dance in dances]
            return True, "success", dances
        except Exception as e:
            return (
                False,
                f"request to choreography client for dances failed. Msg: {e}",
                [],
            )

    def list_all_moves(self) -> Tuple[bool, str, List[str]]:
        """list all uploaded moves"""
        try:
            moves = self._choreography_client.list_all_moves().moves
            moves = [move.name for move in moves]
            return True, "success", moves
        except Exception as e:
            return (
                False,
                f"request to choreography client for moves failed. Msg: {e}",
                [],
            )
        
    def _check_dance_completed(self, status: choreography_sequence_pb2.ChoreographyStatusResponse.Status) -> bool:
        """Check the status message to see if dance is onging/completed, return True if dance is completed"""
        ongoing_states = [
        choreography_sequence_pb2.ChoreographyStatusResponse.Status.STATUS_PREPPING,
        choreography_sequence_pb2.ChoreographyStatusResponse.Status.STATUS_DANCING,
        choreography_sequence_pb2.ChoreographyStatusResponse.Status.STATUS_WAITING_FOR_START_TIME,
        choreography_sequence_pb2.ChoreographyStatusResponse.Status.STATUS_VALIDATING]
        return status not in ongoing_states

    def execute_dance(self, data: str) -> Tuple[bool, str]:
        """Upload and execute dance"""
        if self._robot.is_estopped():
            error_msg = "Robot is estopped. Please use an external E-Stop client"
            "such as the estop SDK example, to configure E-Stop."
            return False, error_msg
        try:
            choreography = choreography_sequence_pb2.ChoreographySequence()
            text_format.Merge(data, choreography)
        except Exception as execp:
            error_msg = "Failed to load choreography. Raised exception: " + str(execp)
            return False, error_msg
        try:
            upload_response = self._choreography_client.upload_choreography(
                choreography, non_strict_parsing=True
            )
        except UnauthenticatedError as err:
            error_msg = "The robot license must contain 'choreography' permissions to upload and execute dances."
            "Please contact Boston Dynamics Support to get the appropriate license file. "
            return False, error_msg
        except ResponseError as err:
            error_msg = "Choreography sequence upload failed. The following warnings were produced: "
            for warn in err.response.warnings:
                error_msg += warn
            return False, error_msg
        try:
            self._robot.power_on()
            routine_name = choreography.name
            client_start_time = time.time()
            start_slice = 0  # start the choreography at the beginning
            response = self._choreography_client.execute_choreography(
                choreography_name=routine_name,
                client_start_time=client_start_time,
                choreography_starting_slice=start_slice,
            )
            response.status
            choreography_sequence_pb2.ExecuteChoreographyResponse.Status.STATUS_OK
            if response.status != choreography_sequence_pb2.ExecuteChoreographyResponse.Status.STATUS_OK:
                return False, f"Issue calling execute_choreography, got response.status: {response.status}"
            total_choreography_slices = 0
            for move in choreography.moves:
                total_choreography_slices += move.requested_slices
                estimated_time_seconds = (
                    total_choreography_slices / choreography.slices_per_minute * 60.0
                )

            start = time.time()
            while time.time() - start < estimated_time_seconds + 0.2:
                choreo_status = self._choreography_client.get_choreography_status()[0]
                status = choreo_status.status
                self._logger.info(str(status))
                if self._check_dance_completed(status):
                    if status == choreography_sequence_pb2.ChoreographyStatusResponse.Status.STATUS_COMPLETED_SEQUENCE:
                        return True, "success"
                    else:
                        return False, f"call to execute_choreography returned unsuccessful status: {status}"
                time.sleep(0.2)        
            raise CommandTimedOutError()

        except CommandTimedOutError:
            raise CommandTimedOutError("Call to execute_choreography did not return completion state in stipulated time")
        except Exception as e:
            return False, f"Error executing dance: {e}"
