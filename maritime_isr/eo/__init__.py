"""The electro-optical loop — Area 5 of the IDEX Challenge 82 brief.

*"The cameras at the radar stations are operated manually by watchkeepers. There
is no automatic capture of an image against a radar or AIS track, no
classification of type and identity against a library, no tagging of the image
to the track, and no alert when what the camera sees disagrees with what the
track claims."*

**Read the four things asked for and notice that only one of them needs
pictures.** Capture without operator intervention, tag the image to a track,
classify against a library, alert on mismatch. Three of those are fusion and
control logic. So this package builds the loop and treats the classifier as a
replaceable component behind an interface:

===========================  ====================================================
:mod:`.camera`               where the cameras are and what each can see
:mod:`.conditions`           light and weather at a position and a moment
:mod:`.cue`                  **the scheduler** — which track, which camera, when
:mod:`.appearance`           the numeric stand-in for pixels, and why there is one
:mod:`.classify`             the swappable classifier and the reference library
:mod:`.capture`              a capture bound to a track, landed as evidence
===========================  ====================================================

**There is no camera in this system and no image exists.** Every capture this
package produces is simulated through the :class:`~.capture.CaptureSource`
seam — in this build by the scenario's own camera simulator, in a deployment by
a driver talking to real hardware. Nothing here has ever seen a photograph, and
every row it writes says so (``capture_mode='simulated'``). The cueing decision,
the tagging, the library and the mismatch rule are real code that would run
unchanged against real imagery; the pixels are the part that is missing, and it
is named rather than implied.

**Nothing in this package may read ground truth** (ADR-019). The camera
simulator knows what is out there because it *is* the world generator; the loop
does not, and ``tests/test_area5.py`` asserts it.
"""
from __future__ import annotations

from .camera import (EOCamera, CameraView, default_camera_network, view,
                     cameras_for_stations)
from .capture import (EOCapture, CaptureSource, ObservedTarget, TABLE,
                      run_captures, land_captures, publish_captures)
from .classify import (ImageClassifier, ImageVerdict, PrototypeClassifier,
                       ReferenceLibrary, SilhouetteClassifier, IMAGERY_TYPES,
                       imagery_group, measure_separability)
from .cue import (CueCandidate, CuePlan, Deferral, Tasking, plan_cueing)

__all__ = [
    "EOCamera", "CameraView", "default_camera_network", "view",
    "cameras_for_stations",
    "CueCandidate", "CuePlan", "Deferral", "Tasking", "plan_cueing",
    "ImageClassifier", "ImageVerdict", "PrototypeClassifier",
    "SilhouetteClassifier", "ReferenceLibrary", "IMAGERY_TYPES",
    "imagery_group", "measure_separability",
    "EOCapture", "CaptureSource", "ObservedTarget", "TABLE",
    "run_captures", "land_captures", "publish_captures",
]
