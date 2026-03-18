#!/usr/bin/env python3

import dbxdriverack as dr

# Constants

## Presets
MinPresetNumber = 1
MaxPresetNumber = 36

## Generic Targets
PresetRecall = "PresetRecall"
PresetName = "PresetName"
PresetCurrent = "PresetCurrent"

## Protocol
PresetsStorage = "\\\\Storage\\Presets"
CurrentPresetTarget = "CurrentPreset"
RecallTarget = "Recall"
PresetNamePrefix = "Name"
PresetNameSubtargetPrefix = f"{PresetNamePrefix}_"


# Helper functions

def validatePresetNumber(preset_num: int | float | str) -> int:
    """Validate and normalize a preset number.

    Parameters
    ----------
    preset_num : int | float | str
        Candidate preset number

    Returns
    -------
    int
        Validated preset number (1-36)

    Raises
    ------
    ValueError
        Preset number is not an integer or out of range
    """

    try:
        preset_num_int = int(preset_num)
    except (ValueError, TypeError):
        raise ValueError(f"Preset number must be an integer, got {preset_num}")

    if not (MinPresetNumber <= preset_num_int <= MaxPresetNumber):
        raise ValueError(
            f"Preset number must be between {MinPresetNumber} and {MaxPresetNumber}"
        )

    return preset_num_int


def getPresetValuesBasePath() -> str:
    """Get the root protocol path for preset values."""

    return f"{PresetsStorage}\\{dr.ProtoValues}"


def getCurrentPresetTargetPath() -> str:
    """Get the protocol path for the current preset number."""

    return f"{getPresetValuesBasePath()}\\{CurrentPresetTarget}"


def getPresetRecallTargetPath() -> str:
    """Get the protocol path for preset recall."""

    return f"{getPresetValuesBasePath()}\\{RecallTarget}"


def getPresetNameSubtarget(preset_num: int) -> str:
    """Get the preset-name subtarget (e.g. Name_1)."""

    preset_num = validatePresetNumber(preset_num)
    return f"{PresetNameSubtargetPrefix}{preset_num}"


def parsePresetNameSubtarget(subtarget: str) -> int:
    """Parse a preset-name subtarget into a validated preset number."""

    if not subtarget.startswith(PresetNameSubtargetPrefix):
        raise ValueError(f"Invalid preset name subtarget: {subtarget}")

    preset_num_suffix = subtarget[len(PresetNameSubtargetPrefix) :]
    return validatePresetNumber(preset_num_suffix)


def isPresetNameSubtarget(subtarget: str) -> bool:
    """Whether a subtarget references a preset name field."""

    return subtarget.startswith(PresetNameSubtargetPrefix)


def getPresetNameTargetPath(preset_num: int) -> str:
    """Get the protocol path for a preset name field."""

    return f"{getPresetValuesBasePath()}\\{getPresetNameSubtarget(preset_num)}"


def isPresetValuesTargetPath(target_path: str) -> bool:
    """Whether a protocol path is under preset values."""

    return target_path.startswith(f"{getPresetValuesBasePath()}\\")


def getPresetSubtarget(target_path: str) -> str:
    """Extract the preset subtarget from a full preset values path."""

    prefix = f"{getPresetValuesBasePath()}\\"
    if not target_path.startswith(prefix):
        raise ValueError(f"Not a preset values target path: {target_path}")

    return target_path[len(prefix) :]


def findPresetByName(preset_names: dict[int, str], name: str) -> int:
    """Find a preset number by its name.

    Parameters
    ----------
    preset_names : dict[int, str]
        Dictionary of preset numbers to names
    name : str
        Preset name to search for

    Returns
    -------
    int
        Preset number (1-36)

    Raises
    ------
    ValueError
        Preset name not found
    """

    for preset_num, preset_name in preset_names.items():
        if preset_name == name:
            return preset_num

    raise ValueError(f"Preset name '{name}' not found")


def getPresetNameByNumber(preset_names: dict[int, str], preset_num: int) -> str:
    """Get the name of a preset by its number.

    Parameters
    ----------
    preset_names : dict[int, str]
        Dictionary of preset numbers to names
    preset_num : int
        Preset number (1-36)

    Returns
    -------
    str
        Preset name

    Raises
    ------
    ValueError
        Preset number out of range
    KeyError
        Preset name not known
    """

    preset_num = validatePresetNumber(preset_num)

    if preset_num not in preset_names:
        raise KeyError(f"Preset name for preset {preset_num} not known")

    return preset_names[preset_num]


class PA2Preset:
    """Represents preset management for a DriveRack PA2.
    Provides methods for recalling presets and managing preset information.
    None of the methods directly affect a live device.

    Attributes
    ----------
    currentPreset : int
        The currently active preset number (1-36)
    presetNames : dict[int, str]
        Dictionary of preset numbers to preset names

    Constants
    ---------
    MinPresetNumber : int
        Minimum preset number
    MaxPresetNumber : int
        Maximum preset number
    """

    def __init__(self) -> None:
        """
        Parameters
        ----------
        None

        Initialized with no preset information.
        """

        self.presetNames: dict[int, str] = {}

    def __str__(self) -> str:
        return f"Presets: Current={getattr(self, 'currentPreset', '?')}"

    def findPresetByName(self, name: str) -> int:
        """Find a preset number by its name.

        Parameters
        ----------
        name : str
            Preset name to search for

        Returns
        -------
        int
            Preset number (1-36)

        Raises
        ------
        ValueError
            Preset name not found
        """

        return findPresetByName(getattr(self, 'presetNames', {}), name)

    def getPresetNameByNumber(self, preset_num: int) -> str:
        """Get the name of a preset by its number.

        Parameters
        ----------
        preset_num : int
            Preset number (1-36)

        Returns
        -------
        str
            Preset name

        Raises
        ------
        ValueError
            Preset number out of range
        KeyError
            Preset name not known
        """

        return getPresetNameByNumber(getattr(self, 'presetNames', {}), preset_num)


class CmdBuilder(dr.CmdBuilder):
    """Subclass of the CmdBuilder network protocol factory for DriveRack PA2."""

    def _generateCmd(self) -> None:
        if self.target == PresetRecall:
            if self.command[0] != dr.ProtoSet:
                raise ValueError("PresetRecall target only supports set commands")

            if "value" not in self.kwargs:
                raise ValueError("Value must be specified for preset recall")

            preset_num = validatePresetNumber(self.kwargs["value"])
            self._addArg(getPresetRecallTargetPath())
            self._addArg(str(preset_num))
        elif self.target == PresetName:
            if self.command[0] != dr.ProtoGet:
                raise ValueError("PresetName target only supports get commands")

            if "preset_num" not in self.kwargs:
                raise ValueError("Preset number must be specified for preset name query")

            preset_num = validatePresetNumber(self.kwargs["preset_num"])
            self._addArg(getPresetNameTargetPath(preset_num))
        elif self.target == PresetCurrent:
            if self.command[0] not in [dr.ProtoGet, dr.ProtoSub]:
                raise ValueError(
                    "PresetCurrent target only supports get and sub commands"
                )

            self._addArg(getCurrentPresetTargetPath())
        else:
            raise ValueError(f"Invalid preset target: {self.target}")
