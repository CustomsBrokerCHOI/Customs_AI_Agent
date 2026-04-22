import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { DraftBanner } from "@/components/DraftBanner";

export const metadata: Metadata = {
  title: "Customs AI Agent — HS CODE 분류",
  description: "관세사용 HS CODE 자동 품목분류 SaaS (Draft 초안 생성)",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko">
      <body className="min-h-screen bg-neutral-50 text-neutral-900">
        <header className="border-b bg-white">
          <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
            <Link href="/" className="text-lg font-semibold">
              Customs AI Agent
            </Link>
            <nav className="flex items-center gap-4 text-sm">
              <Link href="/" className="text-neutral-700 hover:text-neutral-900">
                대시보드
              </Link>
              <Link href="/login" className="text-neutral-700 hover:text-neutral-900">
                로그인
              </Link>
              <Link href="/register" className="text-neutral-700 hover:text-neutral-900">
                회원가입
              </Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-6 py-6 space-y-4">
          <DraftBanner />
          {children}
        </main>
      </body>
    </html>
  );
}
