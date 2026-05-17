import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cn } from "@/lib/utils";

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  asChild?: boolean;
  variant?: "primary" | "ghost" | "danger";
};

export function Button({ className, asChild = false, variant = "primary", ...props }: ButtonProps) {
  const Comp = asChild ? Slot : "button";
  const variants = {
    primary:
      "bg-cyan-300/15 text-cyan-100 border-cyan-300/30 hover:bg-cyan-300/25 shadow-glow",
    ghost:
      "bg-white/5 text-slate-200 border-white/10 hover:bg-white/10",
    danger:
      "bg-rose-500/15 text-rose-100 border-rose-400/30 hover:bg-rose-400/25 shadow-roseGlow"
  };
  return (
    <Comp
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-2xl border px-4 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50",
        variants[variant],
        className
      )}
      {...props}
    />
  );
}
