"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { ApiError, register } from "@/lib/api";

const PASSWORD_HINT =
  "10~128자, 영문 대·소문자·숫자·특수문자 중 2종류 이상 포함.";

export default function RegisterPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError(null);

    if (password !== passwordConfirm) {
      setError("비밀번호 확인이 일치하지 않습니다.");
      return;
    }

    setPending(true);
    try {
      await register({
        email,
        password,
        name: name.trim() ? name.trim() : null,
      });
      router.push("/");
      router.refresh();
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError(err instanceof Error ? err.message : "회원가입 실패");
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="mx-auto max-w-sm space-y-6 py-8">
      <div>
        <h1 className="text-2xl font-bold">회원가입</h1>
        <p className="text-sm text-neutral-600">
          관세사 계정을 생성합니다. 가입 즉시 로그인됩니다.
        </p>
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
          <span className="mb-1 block text-neutral-700">
            이름 <span className="text-neutral-400">(선택)</span>
          </span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={100}
            className="w-full rounded border px-3 py-2 outline-none focus:border-neutral-500"
            placeholder="홍길동"
            autoComplete="name"
          />
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-neutral-700">비밀번호</span>
          <input
            type="password"
            required
            minLength={10}
            maxLength={128}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded border px-3 py-2 outline-none focus:border-neutral-500"
            autoComplete="new-password"
          />
          <span className="mt-1 block text-xs text-neutral-500">{PASSWORD_HINT}</span>
        </label>
        <label className="block text-sm">
          <span className="mb-1 block text-neutral-700">비밀번호 확인</span>
          <input
            type="password"
            required
            minLength={10}
            maxLength={128}
            value={passwordConfirm}
            onChange={(e) => setPasswordConfirm(e.target.value)}
            className="w-full rounded border px-3 py-2 outline-none focus:border-neutral-500"
            autoComplete="new-password"
          />
        </label>
        {error ? <p className="text-sm text-red-600">{error}</p> : null}
        <button
          type="submit"
          disabled={pending}
          className="w-full rounded bg-neutral-900 py-2 text-sm font-medium text-white disabled:opacity-60"
        >
          {pending ? "가입 중..." : "가입하기"}
        </button>
      </form>
      <p className="text-center text-sm text-neutral-600">
        이미 계정이 있나요?{" "}
        <Link href="/login" className="underline hover:text-neutral-900">
          로그인
        </Link>
      </p>
    </section>
  );
}
