import "./globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "AskBase — Project Intel Agent",
  description: "Engineering Project Intelligence Agent-as-a-Service"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
