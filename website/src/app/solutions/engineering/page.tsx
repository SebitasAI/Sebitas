import type { Metadata } from "next";
import SolutionPage from "../_components/SolutionPage";

export const metadata: Metadata = {
  title: "Misterr for Engineering",
  description:
    "Misterr le quita a tu equipo de ingeniería el trabajo que no es código: triage de bugs, resúmenes de PRs, docs que se actualizan solas, y on-call que tiene contexto.",
};

export default function EngineeringPage() {
  return (
    <SolutionPage
      eyebrow="For Engineering"
      title="Más código, menos ruido administrativo"
      lede="Misterr triagea bugs, resume PRs, mantiene los docs alineados con el código y le da contexto a quien esté on-call. Tu equipo programa, Misterr coordina."
      features={[
        {
          title: "Triage de bugs automático",
          description:
            "Cada bug nuevo en Linear / GitHub / Jira: Misterr lo etiqueta por componente, busca duplicados, y pinguea al owner correcto en Slack.",
        },
        {
          title: "Resúmenes de PRs",
          description:
            "PR nuevo abierto, resumen en el thread: qué cambió, qué archivos tocó, qué tests corrieron. El review tiene contexto desde el primer minuto.",
        },
        {
          title: "Docs sincronizadas con releases",
          description:
            "Después de cada deploy, Misterr revisa qué cambió y propone updates al README / docs internas. Vos approvás o editás.",
        },
        {
          title: "On-call con contexto",
          description:
            "Cuando entra un alert, Misterr resume el incidente, busca incidents pasados similares, y pinguea al on-call con el playbook adjunto.",
        },
      ]}
      outcomes={[
        { metric: "−6h", label: "por semana en triage manual" },
        { metric: "3×", label: "más rápido el time-to-context del on-call" },
        { metric: "0", label: "PRs olvidados en review" },
      ]}
    />
  );
}
