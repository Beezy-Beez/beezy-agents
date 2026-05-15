import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/Sidebar";
import TopBar from "@/components/TopBar";
import { ToastProvider } from "@/components/Toast";

export const metadata: Metadata = {
  title: "Beezy Beez · Operations",
  description:
    "Live email-marketing & store operations — revenue, calendar, audiences, flows.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="bg-canvas text-ink antialiased">
        <ToastProvider>
          <Sidebar />
          <div className="ml-60 min-h-screen flex flex-col">
            <TopBar />
            <main className="flex-1 px-7 py-7 max-w-[1480px] w-full">
              {children}
            </main>
          </div>
        </ToastProvider>
      </body>
    </html>
  );
}
