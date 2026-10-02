import InstructorLogin from './InstructorLogin'
import InstructorStudents from './InstructorStudents'

// The instructor side is two screens behind one route: the sign-in, then the
// student list it unlocks. Evaluation (Dashboard.jsx) is reached from a row.
export default function ProfessorAccess({
  signedIn,
  professorId,
  error,
  onMenu,
  onStudent,
  onSignIn,
  onSignOut,
  onView,
  onDismissError,
}) {
  if (!signedIn) {
    return (
      <InstructorLogin
        error={error}
        onMenu={onMenu}
        onStudent={onStudent}
        onSignIn={onSignIn}
        onDismissError={onDismissError}
      />
    )
  }
  return <InstructorStudents professorId={professorId} onMenu={onMenu} onSignOut={onSignOut} onView={onView} />
}
