"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { ApiError, createClassifyJob } from "@/lib/api";

export default function NewClassifyPage() {
  const router = useRouter();
  const [productName, setProductName] = useState("");
  const [description, setDescription] = useState("");
  const [imageUrl, setImageUrl] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      const res = await createClassifyJob({
        product_name: productName.trim(),
        description: description.trim(),
        image_url: imageUrl.trim() ? imageUrl.trim() : undefined,
      });
      router.push(`/classify/${res.job_id}`);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          setError("로그인이 필요합니다.");
        } else {
          setError(err.message);
        }
      } else {
        setError(err instanceof Error ? err.message : "요청 실패");
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">새 분류 요청</h1>
        <p className="text-sm text-neutral-600">
          품명·상세 설명·사진(선택) 을 입력하면 5단계 분류 알고리즘이 초안(Draft) 을
          생성합니다.
        </p>
      </div>

      <form onSubmit={onSubmit} className="space-y-4 rounded-lg border bg-white p-6 shadow-sm">
        <label className="block">
          <span className="mb-1 block text-sm font-medium text-neutral-800">
            품명 <span className="text-red-600">*</span>
          </span>
          <input
            type="text"
            required
            maxLength={500}
            value={productName}
            onChange={(e) => setProductName(e.target.value)}
            placeholder="예: 휴대용 노트북 컴퓨터 (M3 맥북에어 13인치)"
            className="w-full rounded border px-3 py-2 text-sm outline-none focus:border-neutral-500"
          />
        </label>

        <label className="block">
          <span className="mb-1 block text-sm font-medium text-neutral-800">
            상세 설명 <span className="text-red-600">*</span>
          </span>
          <textarea
            required
            maxLength={5000}
            rows={6}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="재질·용도·주요 기능·제조 방식·치수 등을 구체적으로 기재하세요. 정보가 충실할수록 분류 정확도가 올라갑니다."
            className="w-full rounded border px-3 py-2 text-sm outline-none focus:border-neutral-500"
          />
          <p className="mt-1 text-xs text-neutral-500">
            {description.length} / 5000
          </p>
        </label>

        <label className="block">
          <span className="mb-1 block text-sm font-medium text-neutral-800">
            사진 URL (선택)
          </span>
          <input
            type="url"
            maxLength={500}
            value={imageUrl}
            onChange={(e) => setImageUrl(e.target.value)}
            placeholder="https://…"
            className="w-full rounded border px-3 py-2 text-sm outline-none focus:border-neutral-500"
          />
          <p className="mt-1 text-xs text-neutral-500">
            Claude Vision 으로 재질·형태 보조 판독. HTTPS URL 권장.
          </p>
        </label>

        {error ? (
          <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        ) : null}

        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={() => router.back()}
            className="rounded px-4 py-2 text-sm text-neutral-700 hover:bg-neutral-100"
          >
            취소
          </button>
          <button
            type="submit"
            disabled={pending}
            className="rounded bg-neutral-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {pending ? "제출 중..." : "분류 요청"}
          </button>
        </div>
      </form>

      <p className="text-xs text-neutral-500">
        제출 즉시 job_id 가 발급되고, 결과 페이지에서 2-3초 간격으로 상태를 폴링합니다.
        Input Gate 가 정보 부족으로 판단하면 추가 질문이 표시됩니다.
      </p>
    </section>
  );
}
