import type { Metadata } from "next";
import SolutionPage from "../_components/SolutionPage";

export const metadata: Metadata = {
  title: "Misterr for Sales",
  description:
    "Misterr mantiene tu pipeline limpio y en movimiento: CRM al día desde Slack y email, recaps de calls, follow-ups que no se olvidan y proposals draft.",
};

export default function SalesPage() {
  return (
    <SolutionPage
      eyebrow="For Sales"
      title="Pipeline en movimiento, sin que el AE pase 2 horas en el CRM"
      lede="Misterr mantiene HubSpot / Salesforce vivo desde Slack y email, resume calls de Gong, dispara el follow-up al toque y deja drafts de proposals listos para que solo revises."
      features={[
        {
          title: "CRM que se mantiene solo",
          description:
            "Cada email enviado, call recordeado, Slack thread con un prospect: Misterr actualiza la oportunidad en HubSpot/Salesforce sin que escribas nada.",
        },
        {
          title: "Recaps de calls + follow-up al toque",
          description:
            "Después de cada call de Gong: resumen, next steps, action items asignados, y un draft de follow-up email listo para enviar.",
        },
        {
          title: "Pipeline reviews semanales",
          description:
            "Cada lunes, un digest del pipeline: deals at risk, deals que no se movieron, deals listos para cerrar. Solo lo que necesita decisión.",
        },
        {
          title: "Proposals & quotes en draft",
          description:
            "Desde el contexto del deal (call notes, requirements, ICP), Misterr arma una primera versión del proposal. Vos terminás.",
        },
      ]}
      outcomes={[
        { metric: "+25%", label: "tasa de respuesta a follow-ups (los disparamos a tiempo)" },
        { metric: "−2h", label: "por AE por día en CRM updates" },
        { metric: "100%", label: "calls con recap + next steps logueados" },
      ]}
    />
  );
}
