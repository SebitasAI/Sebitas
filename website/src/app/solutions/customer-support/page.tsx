import type { Metadata } from "next";
import SolutionPage from "../_components/SolutionPage";

export const metadata: Metadata = {
  title: "Misterr for Customer Support",
  description:
    "Misterr es el coworker de tu equipo de soporte. Triage de tickets, primeras respuestas, follow-ups con clientes, y handoffs limpios — todo desde Slack.",
};

export default function CustomerSupportPage() {
  return (
    <SolutionPage
      eyebrow="For Customer Support"
      title="Tickets que se mueven solos hasta donde te necesitan"
      lede="Misterr triagea cada conversación de soporte, redacta la primera respuesta, sigue a los clientes que no respondieron y te entrega solo lo que necesita decisión humana."
      features={[
        {
          title: "Triage de tickets en segundos",
          description:
            "Lee cada ticket apenas entra, lo clasifica por urgencia y producto, y lo asigna al canal o agente correcto. Sin reglas que mantener.",
        },
        {
          title: "Borradores de respuesta",
          description:
            "Pulla contexto del CRM + tickets previos del mismo cliente + docs internos y deja una respuesta en draft. Vos editás y mandás.",
        },
        {
          title: "Follow-ups que no se olvidan",
          description:
            "A las 24h sin respuesta del cliente, manda el seguimiento. A las 72h, escala con un resumen al lead. Todo trazable.",
        },
        {
          title: "Handoffs limpios entre turnos",
          description:
            "Al final del shift, Misterr arma el handover note: tickets abiertos, lo que está pending, lo que ya se intentó. Listo para el siguiente.",
        },
      ]}
      outcomes={[
        { metric: "<2 min", label: "first response time promedio" },
        { metric: "−40%", label: "tickets que llegan a tier 2" },
        { metric: "100%", label: "follow-ups disparados en tiempo" },
      ]}
    />
  );
}
