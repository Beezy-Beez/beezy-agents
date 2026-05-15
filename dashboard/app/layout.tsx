import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";

export const metadata: Metadata = {
  title: "Beezy Agents",
  description: "Beezy Beez multi-agent marketing operations dashboard",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="bg-[#faf6ee] text-[#2c2417]">
        <Sidebar />
        <div className="ml-60 min-h-screen flex flex-col">
          <TopBar />
          <main className="flex-1 p-6 max-w-[1400px]">{children}</main>
        </div>
      </body>
    </html>
  );
}
