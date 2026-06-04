import type { Metadata } from "next";
import SolutionPage from "../_components/SolutionPage";

export const metadata: Metadata = {
  title: "Misterr for Operations",
  description:
    "Misterr maneja el trabajo operativo recurrente que nadie quiere ownear: tracking de vendors, automatización de pedidos en Slack, reportes y onboarding.",
};

export default function OperationsPage() {
  return (
    <SolutionPage
      eyebrow="For Operations"
      title="El backbone operativo del equipo, sin contratar otra persona"
      lede="Misterr trackea, reporta y persigue lo que necesita follow-up. Onboarding, renovaciones, reportes semanales, pedidos repetidos en Slack: todo encadenado y sin que nadie tenga que recordar."
      features={[
        {
          title: "Tracking de vendors y renovaciones",
          description:
            "Trackea cada contrato y te pingüea 30 días antes del renewal con el contexto: precio actual, alternativas, último contact.",
        },
        {
          title: "Requests recurrentes, automatizados",
          description:
            "Esa pregunta que te llega cada lunes ('cómo va el revenue?', 'cuándo arranca el nuevo?'): Misterr la responde sola.",
        },
        {
          title: "Reportes operativos semanales",
          description:
            "Pulla data de tus herramientas, arma el status report, lo postea en el canal del leadership cada lunes 9am. Sin recordar el cron.",
        },
        {
          title: "Onboarding y offboarding completos",
          description:
            "Persona nueva: dispara cuentas en cada tool, da accesos, agenda intros. Persona que se va: revoca, archiva, transfiere ownerships.",
        },
      ]}
      outcomes={[
        { metric: "12h", label: "ahorradas por analyst por semana" },
        { metric: "0", label: "renovaciones olvidadas" },
        { metric: "<48h", label: "para onboardear a alguien nuevo end-to-end" },
      ]}
    />
  );
}
