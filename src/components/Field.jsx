import Icon from './Icon'

/**
 * A labelled text input in the design system's field style. Label, hint and
 * error are wired to the input (`htmlFor`, `aria-describedby`, `aria-invalid`)
 * so a screen reader announces them with it.
 *
 * `large` is the sign-in card variant; `end` is an optional control rendered
 * inside the field's right edge (the password reveal button).
 */
export default function Field({ id, label, icon, hint, error, large = false, end, ...inputProps }) {
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined
  return (
    <div className={large ? 'field-lg' : undefined}>
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <div className="field-wrap">
        {icon ? <Icon name={icon} size={18} /> : null}
        <input
          id={id}
          className="field"
          aria-invalid={error ? 'true' : undefined}
          aria-describedby={describedBy}
          {...inputProps}
        />
        {end ? <div className="field-end">{end}</div> : null}
      </div>
      {error ? (
        <p id={`${id}-error`} className="field-error" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="field-hint">
          {hint}
        </p>
      ) : null}
    </div>
  )
}
