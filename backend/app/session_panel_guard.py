"""May a reconnecting client be handed back the session it names?

A session is bound, at creation, to the panel that was attached then
(`panel_id`). ESP32 panels are interchangeable modules, so a resume must not
quietly re-attach that session's workspace/scenario to whatever board is
plugged in now. This is the one comparison, using the identification layer the
rest of the platform already uses (MAC -> PanelRegistry -> PanelDefinition).

PASSIVE, like every other connect-time read: it consults the device monitor's
current verdict and never detects, probes a MAC or opens a port. A session
created with no panel (`panel_id` None: the no-hardware development flow)
holds nothing panel-specific and resumes as before. Anything else resumes only
when the identified panel is exactly the expected one; a missing, unidentified,
unregistered or different board refuses the resume, and the caller then takes
its ordinary new-session path. Refusing touches nothing: the existing session
is left intact for its grace period.
"""

from __future__ import annotations

import logging

from app.hardware.panel_identification import PanelIdentificationService

logger = logging.getLogger(__name__)


def resume_panel_matches(
    expected_panel_id: str | None,
    service: PanelIdentificationService | None = None,
) -> bool:
    if expected_panel_id is None:
        return True
    identification = (service if service is not None else PanelIdentificationService()).identify()
    panel = identification.panel
    if panel is not None and panel.panel_id == expected_panel_id:
        return True
    logger.info(
        "session resume refused: expected panel %s, attached is %s (%s)",
        expected_panel_id,
        panel.panel_id if panel is not None else None,
        identification.status.value,
    )
    return False
