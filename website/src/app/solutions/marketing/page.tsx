import type { Metadata } from "next";
import SolutionPage from "../_components/SolutionPage";

export const metadata: Metadata = {
  title: "Misterr for Marketing & Growth",
  description:
    "Misterr le quita a tu equipo de marketing la pelusa: recaps de campañas, content repurposing, competitor watch y un content calendar que se mantiene solo.",
};

export default function MarketingPage() {
  return (
    <SolutionPage
      eyebrow="For Marketing & Growth"
      title="Menos planillas. Más experimentos que ganan."
      lede="Misterr corre el behind-the-scenes del growth: recap de campañas, drafts de contenido, monitoreo de competidores, y un calendario que se actualiza solo."
      features={[
        {
          title: "Recap automático de campañas",
          description:
            "Pulla números de ads + analytics + email, los junta, y entrega un recap con takeaways en Slack al cierre de cada campaña.",
        },
        {
          title: "Contenido drafted & repurposed",
          description:
            "Un launch, cinco assets. Misterr toma el anuncio y te entrega blog post, hilo de X, post de LinkedIn y email — todo en tu tono.",
        },
        {
          title: "Competitor watch",
          description:
            "Trackea cambios en pricing pages, landings y messaging de competidores. Te pingüea solo cuando algo se mueve.",
        },
        {
          title: "Content calendar que se mantiene",
          description:
            "Actualiza el calendar después de cada publicación, recordatorios a los owners 48h antes, y resume el state al lead semanalmente.",
        },
      ]}
      outcomes={[
        { metric: "5×", label: "más contenido por campaña al repurposear" },
        { metric: "<24h", label: "desde competidor-move hasta tu reacción" },
        { metric: "−8h", label: "por semana en reporting manual" },
      ]}
    />
  );
}
