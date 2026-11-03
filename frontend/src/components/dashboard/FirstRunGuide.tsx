import React from "react";
import {
  Building2,
  Sliders,
  Play,
  ShieldCheck,
  ArrowRight,
} from "lucide-react";
import { useApp } from "../../context/AppContext";
import { Button } from "../common/Button";

export const FirstRunGuide: React.FC = () => {
  const { setActiveTab } = useApp();

  return (
    <div
      className="bg-gradient-to-br from-teal-900/30 via-slate-900 to-slate-900 border border-teal-500/30 rounded-2xl p-6 sm:p-8 shadow-xl text-slate-100 space-y-6"
      data-testid="first-run-guide"
    >
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 border-b border-slate-800 pb-5">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-teal-500/20 text-teal-300 border border-teal-500/30">
              Welcome to HawkOffer
            </span>
            <span className="text-xs text-slate-400">Local-First Desktop Ingestion</span>
          </div>
          <h2 className="text-xl sm:text-2xl font-bold text-white tracking-tight">
            Get Started with Automated Career Monitoring
          </h2>
          <p className="text-sm text-slate-300 max-w-2xl">
            HawkOffer runs locally on your machine. It polls public ATS endpoints (Greenhouse &amp; Lever),
            filters opportunities with zero AI hallucinations using deterministic criteria, and delivers
            promoted notifications directly to you.
          </p>
        </div>
      </div>

      {/* 3-Step Guided Path */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Step 1 */}
        <div className="bg-slate-800/80 border border-slate-700/80 rounded-xl p-5 flex flex-col justify-between space-y-4 hover:border-teal-500/40 transition-colors">
          <div className="space-y-2.5">
            <div className="w-8 h-8 rounded-lg bg-teal-600/30 border border-teal-500/40 text-teal-300 flex items-center justify-center font-bold text-sm">
              1
            </div>
            <h3 className="font-semibold text-white flex items-center gap-2">
              <Building2 className="w-4 h-4 text-teal-400" />
              Add Monitored Company
            </h3>
            <p className="text-xs text-slate-400 leading-relaxed">
              Register company career sites using their Greenhouse or Lever board slug.
            </p>
            <div className="p-2.5 bg-slate-950/80 rounded-lg border border-slate-800 space-y-1">
              <span className="text-[10px] uppercase font-bold text-slate-400 tracking-wider">
                Common board slugs:
              </span>
              <div className="flex flex-wrap gap-1.5 pt-0.5">
                <span className="text-xs font-mono bg-slate-800 px-2 py-0.5 rounded text-teal-300">
                  stripe (Greenhouse)
                </span>
                <span className="text-xs font-mono bg-slate-800 px-2 py-0.5 rounded text-teal-300">
                  figma (Greenhouse)
                </span>
                <span className="text-xs font-mono bg-slate-800 px-2 py-0.5 rounded text-teal-300">
                  palantir (Greenhouse)
                </span>
              </div>
            </div>
          </div>
          <Button
            variant="primary"
            size="sm"
            onClick={() => setActiveTab("companies")}
            className="w-full flex items-center justify-center gap-1.5"
          >
            Add Company <ArrowRight className="w-3.5 h-3.5" />
          </Button>
        </div>

        {/* Step 2 */}
        <div className="bg-slate-800/80 border border-slate-700/80 rounded-xl p-5 flex flex-col justify-between space-y-4 hover:border-teal-500/40 transition-colors">
          <div className="space-y-2.5">
            <div className="w-8 h-8 rounded-lg bg-teal-600/30 border border-teal-500/40 text-teal-300 flex items-center justify-center font-bold text-sm">
              2
            </div>
            <h3 className="font-semibold text-white flex items-center gap-2">
              <Sliders className="w-4 h-4 text-teal-400" />
              Set Preference Profile
            </h3>
            <p className="text-xs text-slate-400 leading-relaxed">
              Define your exact criteria: title keywords, seniority levels, preferred locations, and
              exclusion terms. Only strictly matching roles get promoted.
            </p>
            <div className="p-2.5 bg-slate-950/80 rounded-lg border border-slate-800 text-xs text-slate-400">
              Deterministic rule evaluation ensures zero noise, false positives, or LLM drift.
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setActiveTab("profiles")}
            className="w-full flex items-center justify-center gap-1.5"
          >
            Configure Profile <ArrowRight className="w-3.5 h-3.5" />
          </Button>
        </div>

        {/* Step 3 */}
        <div className="bg-slate-800/80 border border-slate-700/80 rounded-xl p-5 flex flex-col justify-between space-y-4 hover:border-teal-500/40 transition-colors">
          <div className="space-y-2.5">
            <div className="w-8 h-8 rounded-lg bg-teal-600/30 border border-teal-500/40 text-teal-300 flex items-center justify-center font-bold text-sm">
              3
            </div>
            <h3 className="font-semibold text-white flex items-center gap-2">
              <Play className="w-4 h-4 text-teal-400" />
              Run Scan &amp; Monitor
            </h3>
            <p className="text-xs text-slate-400 leading-relaxed">
              Trigger your first scan immediately or let the background orchestrator poll periodically.
              New matching opportunities arrive in your Notifications feed.
            </p>
            <div className="p-2.5 bg-slate-950/80 rounded-lg border border-slate-800 text-xs text-slate-400">
              Fair sequential promotion allocates backlog slots across all configured companies.
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setActiveTab("monitoring")}
            className="w-full flex items-center justify-center gap-1.5"
          >
            Monitoring Controls <ArrowRight className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>

      {/* Local-First Architecture Guarantee */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 p-3.5 bg-slate-950/60 rounded-xl border border-slate-800/80 text-xs text-slate-400">
        <div className="flex items-center gap-2 text-teal-300">
          <ShieldCheck className="w-4 h-4 text-teal-400 flex-shrink-0" />
          <span className="font-semibold text-slate-200">100% Local-First &amp; Private</span>
        </div>
        <p className="text-slate-400 text-xs">
          HawkOffer stores all data in your local SQLite database. No external tracking, no cloud telemetry.
        </p>
      </div>
    </div>
  );
};
