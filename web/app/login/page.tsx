"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { ApiError, login } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      await login({ email, password });
      router.push("/");
      router.refresh();
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError(err instanceof Error ? err.message : "로그인 실패");
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="mx-auto max-w-sm space-y-6 py-8">
      <div>
        <h1 className="text-2xl font-bold">로그인</h1>
        <p className="text-sm text-neutral-600">관세사 계정으로 로그인하세요.</p>
      </div>
      <form onSubmit={onSubmit} className="space-y-3">
        <label className="block text-sm">
          <span className="mb-1 block text-neutral-700">이메일</span>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded border px-3 py-2 outline-none focus:border-neutral-500"
            placeholder="you@firm.co.kr"
            autoComplete="email"
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-neutral-700">비밀번호</span>
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded border px-3 py-2 outline-none focus:border-neutral-500"
            autoComplete="current-password"
          />
        </label>
        {error ? <p className="text-sm text-red-600">{error}</p> : null}
        <button
          type="submit"
          disabled={pending}
          className="w-full rounded bg-neutral-900 py-2 text-sm font-medium text-white disabled:opacity-60"
        >
          {pending ? "로그인 중..." : "로그인"}
        </button>
      </form>
    </section>
  );
}
