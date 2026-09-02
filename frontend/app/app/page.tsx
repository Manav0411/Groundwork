import type { Metadata } from "next";
import { Workspace } from "@/components/app/Workspace";

export const metadata: Metadata = {
  title: "Groundwork — Workspace",
  description: "Ask questions about a project and get answers with their sources attached."
};

export default function AppPage() {
  return <Workspace />;
}
