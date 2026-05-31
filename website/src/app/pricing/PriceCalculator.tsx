"use client";

import { useState } from "react";

// TODO pricing: la tarifa $2,50/1.000 créditos está copiada del competidor como punto de partida (v1).
// El costo real de servir y la cantidad de créditos-por-tarea NO están confirmados. Validar con datos
// reales antes de tratar estos números como definitivos. El default de 50 créditos/tarea es un placeholder.

// ── Modelo de precio (ajustar aquí, en un solo lugar) ───────────────────────
const CONFIG = {
  costPerCredit: 0.0025, // USD por crédito ($2,50 por 1.000 créditos)
  ratePer1000: 2.5, // USD por cada 1.000 créditos (display)
  included: {
    usd: 50, // incluido en el plan base / mes
    credits: 20000, // créditos incluidos / mes
  },
  slider: {
    min: 20000,
    max: 2000000,
    step: 10000,
    initial: 40000,
  },
  defaultCreditsPerTask: 50, // placeholder — define el margen, no la tarifa
  salesHref: "#enterprise", // flujo de contacto / ventas (Enterprise)
};

// Formato es-ES con separador de miles, siempre enteros.
const nf = new Intl.NumberFormat("es-ES");
const fmt = (n: number) => nf.format(Math.round(n));

export default function PriceCalculator() {
  const [credits, setCredits] = useState(CONFIG.slider.initial);
  const [creditsPerTask, setCreditsPerTask] = useState(
    CONFIG.defaultCreditsPerTask,
  );

  const cost = Math.round(credits * CONFIG.costPerCredit);
  const atMax = credits >= CONFIG.slider.max;
  const tasks =
    creditsPerTask > 0 ? Math.round(credits / creditsPerTask) : null;

  // posición del thumb (0–100%) para pintar el relleno del track
  const pct =
    ((credits - CONFIG.slider.min) /
      (CONFIG.slider.max - CONFIG.slider.min)) *
    100;

  return (
    <div className="flex w-full max-w-[720px] flex-col gap-[32px] rounded-[20px] border border-[#191919] bg-white p-[28px] shadow-[0px_4px_0px_0px_#626262] sm:p-[40px]">
      {/* Badge superior */}
      <div className="flex justify-center">
        <span className="rounded-full bg-[#faf5f1] px-[18px] py-[8px] text-center font-[family-name:var(--font-inter)] text-[14px] font-medium tracking-[-0.2px] text-[#626262] sm:text-[15px]">
          Incluido en tu plan:{" "}
          <span className="font-semibold text-[#191919]">
            ${fmt(CONFIG.included.usd)} = {fmt(CONFIG.included.credits)} créditos
          </span>{" "}
          / mes
        </span>
      </div>

      {/* Slider */}
      <div className="flex w-full flex-col gap-[14px]">
        <input
          type="range"
          min={CONFIG.slider.min}
          max={CONFIG.slider.max}
          step={CONFIG.slider.step}
          value={credits}
          onChange={(e) => setCredits(Number(e.target.value))}
          aria-label="Créditos por mes"
          className="price-slider h-[8px] w-full cursor-pointer appearance-none rounded-full outline-none"
          style={{
            background: `linear-gradient(90deg, #ff5200 0%, #ff5200 ${pct}%, #ece7e1 ${pct}%, #ece7e1 100%)`,
          }}
        />
        <div className="flex justify-between font-[family-name:var(--font-inter)] text-[12px] text-[#9a9a9a]">
          <span>{fmt(CONFIG.slider.min)}</span>
          <span>{fmt(CONFIG.slider.max)}</span>
        </div>
      </div>

      {/* Lecturas: metric cards o mensaje Enterprise al tope */}
      {atMax ? (
        <div className="flex flex-col items-center gap-[10px] rounded-[14px] border border-[#ff5200] bg-[#fff3ec] px-[24px] py-[28px] text-center">
          <p className="font-[family-name:var(--font-lexend)] text-[22px] font-semibold tracking-[-0.6px] text-[#191919]">
            ¿Necesitas más volumen?
          </p>
          <a
            href={CONFIG.salesHref}
            className="font-[family-name:var(--font-inter)] text-[18px] font-semibold text-[#ff5200] underline-offset-4 hover:underline"
          >
            Habla con ventas →
          </a>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-[16px] sm:grid-cols-2">
          <MetricCard label="Créditos / mes" value={fmt(credits)} />
          <MetricCard label="Costo / mes" value={`$${fmt(cost)}`} accent />
        </div>
      )}

      {/* Traductor de valor */}
      <div className="flex flex-col gap-[10px] border-t border-[#ece7e1] pt-[28px]">
        <div className="flex flex-wrap items-center gap-x-[8px] gap-y-[8px] font-[family-name:var(--font-inter)] text-[16px] text-[#191919] sm:text-[18px]">
          <span>Supongamos: 1 tarea =</span>
          <input
            type="number"
            min={1}
            step={1}
            value={creditsPerTask}
            onChange={(e) => setCreditsPerTask(Math.max(0, Number(e.target.value)))}
            aria-label="Créditos por tarea"
            className="w-[88px] rounded-[10px] border border-[#d9d4cd] bg-white px-[12px] py-[6px] text-center font-semibold text-[#191919] outline-none focus:border-[#ff5200]"
          />
          <span>créditos</span>
          <span className="text-[#9a9a9a]">→</span>
          <span className="font-semibold text-[#191919]">
            ≈ {tasks !== null ? fmt(tasks) : "—"} tareas / mes
          </span>
        </div>
        <p className="font-[family-name:var(--font-inter)] text-[13px] leading-[1.5] text-[#9a9a9a]">
          Define este número: es lo que decide tu margen, no la tarifa por
          crédito.
        </p>
      </div>
    </div>
  );
}

function MetricCard({
  label,
  value,
  accent = false,
}: {
  label: string;
  value: string;
  accent?: boolean;
}) {
  return (
    <div className="flex flex-col gap-[6px] rounded-[14px] border border-[#ece7e1] bg-[#faf5f1] px-[24px] py-[22px]">
      <span className="font-[family-name:var(--font-inter)] text-[14px] font-medium text-[#626262]">
        {label}
      </span>
      <span
        className={`font-[family-name:var(--font-lexend)] text-[40px] font-semibold leading-[1.05] tracking-[-1.6px] ${
          accent ? "text-[#ff5200]" : "text-[#191919]"
        }`}
      >
        {value}
      </span>
    </div>
  );
}
