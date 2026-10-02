import useBackendStatus, { BACKEND_STATUS } from '../hooks/useBackendStatus'

const LABEL = {
  [BACKEND_STATUS.CHECKING]: 'Checking trainer hub…',
  [BACKEND_STATUS.ONLINE]: 'Trainer hub online',
  [BACKEND_STATUS.OFFLINE]: 'Trainer hub unreachable',
}

const DOT = {
  [BACKEND_STATUS.CHECKING]: 'dot',
  [BACKEND_STATUS.ONLINE]: 'dot is-ok',
  [BACKEND_STATUS.OFFLINE]: 'dot is-bad',
}

/**
 * Whether the trainer backend is reachable, as a small pill. Reports only what
 * the page can verify (the backend's liveness probe and the host it was loaded
 * from) — never a panel, Wi-Fi or USB state, which this screen cannot know.
 */
export default function BackendStatusPill() {
  const status = useBackendStatus()
  return (
    <div className="status-pill" role="status">
      <span className="status-pill-item">
        <span className={DOT[status]} aria-hidden="true" />
        {LABEL[status]}
      </span>
      <span className="status-pill-sep" aria-hidden="true" />
      <span className="status-pill-item">
        Host: <b>{window.location.hostname}</b>
      </span>
    </div>
  )
}
