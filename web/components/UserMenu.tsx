"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, fetchMe, logout } from "@/lib/api";
import type { UserMe } from "@/lib/types";

type State =
  | { kind: "loading" }
  | { kind: "anon" }
  | { kind: "authed"; user: UserMe };

export function UserMenu() {
  const router = useRouter();
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const u = await fetchMe();
        if (!cancelled) setState({ kind: "authed", user: u });
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) {
          setState({ kind: "anon" });
        } else {
          // 네트워크 오류 등 — 익명 취급
          setState({ kind: "anon" });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function onLogout() {
    try {
      await logout();
    } catch {
      // 서버 장애여도 로컬 상태는 익명으로
    }
    setState({ kind: "anon" });
    router.push("/");
    router.refresh();
  }

  if (state.kind === "loading") {
    return <span className="text-neutral-400">···</span>;
  }

  if (state.kind === "anon") {
    return (
      <>
        <Link href="/login" className="text-neutral-700 hover:text-neutral-900">
          로그인
        </Link>
        <Link href="/register" className="text-neutral-700 hover:text-neutral-900">
          회원가입
        </Link>
      </>
    );
  }

  const display = displayNameOf(state.user);
  return (
    <>
      <span className="text-neutral-800">
        <span className="font-medium">{display}</span>
        <span className="text-neutral-500">님</span>
      </span>
      <button
        type="button"
        onClick={onLogout}
        className="text-neutral-700 hover:text-neutral-900"
      >
        로그아웃
      </button>
    </>
  );
}

function displayNameOf(u: UserMe): string {
  if (u.name && u.name.trim()) return u.name;
  const local = u.email.split("@")[0];
  return local || u.email;
}
