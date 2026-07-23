import { DocumentShell } from "../_shared/DocumentShell";

export default function LegalLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return <DocumentShell lang="en">{children}</DocumentShell>;
}
