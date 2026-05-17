import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NexusOS-Ultra",
  description: "AI Workspace and Cloud Browser Platform"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body>{children}</body>
    </html>
  );
}
