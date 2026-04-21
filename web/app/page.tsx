export default function DashboardPage() {
  return (
    <section className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">대시보드</h1>
        <p className="text-sm text-neutral-600">
          관세사 분류 워크플로우 진입점. 분류 요청 · 결과 히스토리 · HS 마스터 조회.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Card
          title="새 분류 요청"
          description="품명·설명·사진으로 HS CODE 초안 생성"
          href="/classify/new"
          disabled
          badge="Week 3"
        />
        <Card
          title="분류 히스토리"
          description="내 분류 잡 상태 · 결과 · 관세사 확인 여부"
          href="/classify"
          disabled
          badge="Week 3"
        />
        <Card
          title="HS 마스터 조회"
          description="10자리 HS 부호로 품명·세율·적용기간 조회"
          href="/hs"
          disabled
          badge="Week 3"
        />
      </div>
    </section>
  );
}

function Card(props: {
  title: string;
  description: string;
  href: string;
  disabled?: boolean;
  badge?: string;
}) {
  const base =
    "block rounded-lg border bg-white p-4 shadow-sm transition hover:shadow-md";
  const disabled = "pointer-events-none opacity-60";
  return (
    <a href={props.href} className={`${base} ${props.disabled ? disabled : ""}`}>
      <div className="flex items-center justify-between">
        <h2 className="font-semibold">{props.title}</h2>
        {props.badge ? (
          <span className="rounded bg-neutral-100 px-2 py-0.5 text-xs text-neutral-600">
            {props.badge}
          </span>
        ) : null}
      </div>
      <p className="mt-1 text-sm text-neutral-600">{props.description}</p>
    </a>
  );
}
