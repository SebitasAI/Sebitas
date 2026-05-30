// Filled nav icons ported from Antiff's nav-icons.tsx. The dashboard uses
// these for the main sidebar tabs because the lucide-react outline icons
// looked too thin against the active-state orange. All icons use
// `fill="currentColor"` so the parent's text color (active=orange,
// inactive=neutral-600) tints the whole shape.

export type NavIcon = React.FC<{ className?: string }>;

const cn = (...classes: (string | false | undefined)[]) =>
  classes.filter(Boolean).join(" ");

export function HomeIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="20"
      height="21"
      fill="none"
      viewBox="0 0 20 21"
      className={cn("size-5", className)}
    >
      <path
        fill="currentColor"
        d="m16.309 6.603-5.251-3.99a1.76 1.76 0 0 0-2.118 0l-5.251 3.99C3.257 6.933 3 7.454 3 7.996v7.254A2.75 2.75 0 0 0 5.75 18h8.499a2.75 2.75 0 0 0 2.75-2.75V7.996c0-.542-.258-1.062-.69-1.393M13.249 15h-6.5a.75.75 0 0 1 0-1.5h6.5a.75.75 0 0 1 0 1.5"
      />
    </svg>
  );
}

export function IntegrationsIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="20"
      height="21"
      fill="none"
      viewBox="0 0 20 21"
      className={cn("size-5", className)}
    >
      <path
        fill="currentColor"
        d="M16.164 9.665a.75.75 0 0 0 1.086-.67V7.251a2.75 2.75 0 0 0-2.75-2.75h-1.775c.01-.083.025-.164.025-.25 0-1.103-.897-2-2-2s-2 .897-2 2c0 .086.015.167.025.25H7a2.75 2.75 0 0 0-2.75 2.75v1.775c-.083-.011-.164-.025-.25-.025-1.103 0-2 .897-2 2s.897 2 2 2c.086 0 .167-.015.25-.025v1.775A2.75 2.75 0 0 0 7 17.501h1.744c.26 0 .501-.135.638-.355a.75.75 0 0 0 .033-.73 1.5 1.5 0 0 1-.165-.665c0-.827.673-1.5 1.5-1.5s1.5.673 1.5 1.5q0 .333-.166.666a.75.75 0 0 0 .672 1.084H14.5a2.75 2.75 0 0 0 2.75-2.75v-1.744a.75.75 0 0 0-1.086-.67c-1.027.515-2.164-.285-2.164-1.335s1.134-1.85 2.164-1.335z"
      />
    </svg>
  );
}

// Skills icon: filled spark/star to evoke "AI skills". Designed in the
// same visual weight as Antiff's other icons (rounded, single fill).
export function SkillsIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="20"
      height="20"
      fill="none"
      viewBox="0 0 20 20"
      className={cn("size-5", className)}
    >
      <path
        fill="currentColor"
        d="M10 1.75a.75.75 0 0 1 .701.48l1.382 3.557a3.75 3.75 0 0 0 2.13 2.13l3.557 1.382a.75.75 0 0 1 0 1.402l-3.557 1.382a3.75 3.75 0 0 0-2.13 2.13L10.7 17.77a.75.75 0 0 1-1.402 0l-1.382-3.557a3.75 3.75 0 0 0-2.13-2.13L2.23 10.7a.75.75 0 0 1 0-1.402l3.557-1.382a3.75 3.75 0 0 0 2.13-2.13L9.3 2.23A.75.75 0 0 1 10 1.75"
      />
    </svg>
  );
}

// Spaces icon: filled 2x2 grid of rounded squares. Same visual rhythm
// as Antiff's other nav icons.
export function SpacesIcon({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="20"
      height="20"
      fill="none"
      viewBox="0 0 20 20"
      className={cn("size-5", className)}
    >
      <path
        fill="currentColor"
        d="M4.75 2.5a2.25 2.25 0 0 0-2.25 2.25v2.5A2.25 2.25 0 0 0 4.75 9.5h2.5A2.25 2.25 0 0 0 9.5 7.25v-2.5A2.25 2.25 0 0 0 7.25 2.5zM12.75 2.5a2.25 2.25 0 0 0-2.25 2.25v2.5a2.25 2.25 0 0 0 2.25 2.25h2.5a2.25 2.25 0 0 0 2.25-2.25v-2.5a2.25 2.25 0 0 0-2.25-2.25zM4.75 10.5a2.25 2.25 0 0 0-2.25 2.25v2.5a2.25 2.25 0 0 0 2.25 2.25h2.5a2.25 2.25 0 0 0 2.25-2.25v-2.5a2.25 2.25 0 0 0-2.25-2.25zM12.75 10.5a2.25 2.25 0 0 0-2.25 2.25v2.5a2.25 2.25 0 0 0 2.25 2.25h2.5a2.25 2.25 0 0 0 2.25-2.25v-2.5a2.25 2.25 0 0 0-2.25-2.25z"
      />
    </svg>
  );
}
