import { useEffect, useState } from "react";
import { BookOpen, CheckCircle2, Eye, Info, Sparkles } from "lucide-react";
import { useAuth } from "../auth";
import { Badge, PageHeader, Panel, SectionTitle, cx } from "../components";

interface FigureAnnotation {
  x: number;
  y: number;
  width: number;
  height: number;
  title: string;
  description: string;
  badgeRight?: boolean;
}

interface FigureData {
  src: string;
  alt: string;
  caption: string;
  annotations: FigureAnnotation[];
  imageWidth: number;
  imageHeight: number;
}

interface TutorialStep {
  title: string;
  summary: string;
  figures: FigureData[];
  note?: string;
}

const screenshotSizes: Record<string, readonly [number, number]> = {
  "admin-batch-0418-missing": [1425, 891],
  "admin-batch-0419-ready": [1425, 891],
  "admin-batch-0418-manage": [1010, 900],
  "admin-tasks": [1425, 891],
  "admin-writing": [1425, 891],
  "admin-preview-article": [1425, 891],
  "admin-reports": [1425, 891],
  "admin-report-match": [1425, 891],
  "admin-report-article": [1425, 891],
  "member-claim": [1265, 712],
  "member-enter": [1265, 712],
  "member-writing": [1265, 712],
  "member-writing-actions": [1265, 712],
  "member-previews": [1265, 712],
  "member-preview-article": [1265, 712],
  "member-reports": [1265, 712],
  "member-report-match": [1265, 712],
};

const figure = (
  src: string,
  alt: string,
  caption: string,
  annotations: FigureAnnotation[],
  imageWidth?: number,
  imageHeight?: number,
): FigureData => {
  const [sourceWidth, sourceHeight] = screenshotSizes[src] ?? [1440, 900];
  return {
    src: `/tutorial/${src}.webp`,
    alt,
    caption,
    annotations,
    imageWidth: imageWidth ?? sourceWidth,
    imageHeight: imageHeight ?? sourceHeight,
  };
};

const marker = (
  x: number,
  y: number,
  width: number,
  height: number,
  title: string,
  description: string,
  badgeRight = false,
): FigureAnnotation => ({ x, y, width, height, title, description, badgeRight });

const adminSteps: TutorialStep[] = [
  {
    title: "创建前瞻批次",
    summary: "先把需要处理的比赛日期和赛事组合成批次。",
    figures: [figure("admin-create", "管理员创建前瞻批次页面", "创建批次页面（管理员权限）", [
      marker(296.8, 284.2, 449, 45.2, "比赛日期与“添加”", "选好日期后必须点击“添加”，日期才会进入已选列表；只停留在日期输入框中不会生效。"),
      marker(296.8, 542, 449, 65.4, "赛事", "勾选需要处理的赛事。系统会把每个已选日期与每个已选赛事分别组合成独立批次。"),
      marker(296.8, 627.4, 449, 38, "创建批次", "点击后查询比赛并创建全部组合；已有的日期与赛事组合会直接复用，不会重复创建。"),
    ])],
  },
  {
    title: "查看前瞻批次",
    summary: "在列表中先判断批次是否完整，再选择刷新、渲染或进入详情。",
    figures: [figure("admin-batches", "管理员前瞻批次列表中的待完善和可发布状态", "管理员前瞻批次列表", [
      marker(272.8, 393.1, 1126.4, 65, "存在缺项", "存在缺项意味着仍需补充内容。通常一篇前瞻只需要手动补充标题和各场比赛的前瞻正文；有缺项的批次仍然可以渲染文章并预览。"),
      marker(272.8, 328.1, 1126.4, 65, "已经完善", "只有已完善的批次，才可以把渲染后的文章推送至微信公众号草稿箱。如果前瞻内容后来发生改变，已有文章会过期，此时需要重新渲染。"),
      marker(1097.8, 341.6, 285.4, 38, "行末操作", "刷新按钮重新获取比赛及天气；文档按钮渲染前瞻文章；箭头进入批次详情。刷新不会删除已经填写的正文。"),
    ])],
    note: "天气通常不需要手动填写。建议在比赛前几天点击行末“重新查询”，系统会重新获取较新的天气；离比赛太远时没有天气是正常情况。",
  },
  {
    title: "检查批次详情并开放任务",
    summary: "分别查看缺项与完整状态，随后管理任务开放和低频设置。",
    figures: [
      figure("admin-batch-0418-missing", "管理员批次详情中的9项完整性缺项", "存在缺项的批次详情", [
        marker(272, 169.7, 1112.8, 75.8, "状态摘要", "“待完善”说明生成完整文章所需的数据仍未齐全；本批次有 2 场有效比赛，当前任务均未开放。"),
        marker(272, 265.5, 1112.8, 140.3, "9 项缺失", "缺少标题、天气、编辑、责编、审核，以及环境 vs 探微、求真 vs 新雅两场比赛各自的作者与正文。"),
        marker(1279.2, 103.7, 105.6, 38, "文章预览", "进入最近一次渲染记录。即使批次有缺项也能检查占位版式，但它不会成为微信草稿候选。"),
      ]),
      figure("admin-batch-0419-ready", "管理员批次详情显示内容完整可以发布", "已经完善的批次详情", [
        marker(272, 169.7, 1112.8, 75.8, "可发布", "有效比赛、正文、作者、天气和人员均已齐全；1 场任务当前开放。"),
        marker(272, 261.5, 1112.8, 45.8, "完整提示", "出现绿色提示代表可以渲染完整文章，并在文章保持最新时加入微信草稿。"),
        marker(1279.2, 103.7, 105.6, 38, "文章预览", "查看已渲染文章；若之后修改标题、天气、人员或正文，需要重新渲染。"),
      ]),
      figure("admin-batch-0418-manage", "批次详情中的比赛任务按钮和人员天气设置", "同一详情页下方的任务与管理设置", [
        marker(293, 117, 717, 104.2, "比赛", "点击比赛卡进入写作页。卡片同时显示开放状态、开球信息和认领人，方便检查任务是否已正确发布。"),
        marker(296.8, 259, 220, 38, "开放 / 关闭全部任务", "开放后普通用户可以领取；关闭后停止新领取，但认领人、署名和正文都会保留，已认领用户仍可继续修改。"),
        marker(272, 486, 360.3, 414, "人员设置", "新建批次会继承系统中的编辑、责编、审核默认值，通常无需逐批手动填写；只有临时换人时才在这里修改。"),
      ], 1010),
    ],
    note: "缺项页面故意保留未填写内容用于教学。一般工作中，人员由系统默认配置自动带入；天气在比赛前几天通过“重新查询”更新即可，只有自动数据不合适时才手动保存天气。",
  },
  {
    title: "管理前瞻任务",
    summary: "在任务中心集中查看认领情况，并按需转交或释放任务。",
    figures: [figure("admin-tasks", "管理员任务中心中的男足任务及转交释放按钮", "管理员看到的全部开放任务", [
      marker(272, 489.5, 1112.8, 330.3, "全部开放任务", "这里列出当前所有开放任务及认领人。管理员可以直接进入比赛，也可以处理负责人变化。"),
      marker(313.6, 740.2, 78.6, 38, "转交", "把任务负责人改为另一个启用账号，同时保留现有署名和正文，适合临时调整分工。"),
      marker(399.2, 740.2, 55.6, 38, "释放", "清空认领人和署名，使任务重新可领取；已经填写的正文不会被删除。"),
    ])],
  },
  {
    title: "查看与管理比赛写作",
    summary: "管理员可进入任何比赛，核对署名、正文和保存状态。",
    figures: [figure("admin-writing", "管理员查看车辆对未央写作页面", "车辆 vs 未央的比赛写作页", [
      marker(296.8, 301.8, 913.5, 43.2, "署名", "管理员可以修正作者署名；修改署名同样会使当前前瞻文章过期，需要重新渲染。"),
      marker(296.8, 384, 1063.2, 402.4, "前瞻正文", "一个或多个换行都会分段。页面保留当前真实正文，修改时不会自动保存。"),
      marker(296.8, 798.4, 1063.2, 38, "撤销修改 / 保存正文", "内容变化后“保存正文”才会启用；保存会增加版本号，若他人同时修改会提示版本冲突而不会静默覆盖。"),
    ])],
  },
  {
    title: "渲染并检查前瞻文章",
    summary: "将完整批次生成最终公众号排版。",
    figures: [figure("admin-preview-article", "男足前瞻文章信息和最终效果", "当前完整前瞻文章的实际预览", [
      marker(1279.2, 103.7, 105.6, 38, "重新渲染", "批次内容发生变化后点击此按钮生成新记录；内容完全相同时会复用现有版本。"),
      marker(272, 169.7, 300, 450.4, "文章信息", "核对版本、完整状态、标题、作者、生成时间和模板。“完整，可发布”才会进入微信草稿候选。"),
      marker(592, 169.7, 792.8, 721.3, "最终效果", "右侧 iframe 就是后端模板真实生成的公众号排版；可滚动检查，也可点击“全屏预览”。"),
    ])],
  },
  {
    title: "管理战报批次",
    summary: "赛后展开批次，先检查单场战报，再处理整批文章。",
    figures: [figure("admin-reports", "管理员展开男足战报批次列表", "已展开的男足战报批次", [
      marker(288.8, 343.1, 98.4, 35, "展开批次", "点击日期行可展开或收起比赛，不会修改数据。"),
      marker(1226.8, 341.6, 141.2, 38, "行末操作", "依次为重新查询比赛数据、渲染整批战报文章、预览整批文章；只有管理员会看到前两个按钮。"),
      marker(288.8, 405.5, 1079.2, 105.6, "进入单场战报", "比赛卡显示完赛状态和战报是否已生成，点击后进入单场战报页面。"),
    ])],
  },
  {
    title: "渲染单场战报",
    summary: "为已完赛比赛生成并核对战报图片。",
    figures: [figure("admin-report-match", "车辆对未央单场战报页面", "车辆 vs 未央的真实战报图片", [
      marker(1279.2, 103.7, 105.6, 38, "重新渲染", "实时查询比赛事件并重新生成战报；数据未变化时复用现有结果。"),
      marker(296.8, 261.2, 1063.2, 629.8, "战报效果", "检查比分、阵容、进球、换人和红黄牌。遇到错误会停止生成并显示诊断，警告则保留结果供人工核对。"),
    ])],
  },
  {
    title: "检查批次战报文章",
    summary: "确认整批战报的版本、完整状态与最终排版。",
    figures: [figure("admin-report-article", "男足批次战报文章信息和最终效果", "当前批次战报文章的实际预览", [
      marker(1279.2, 103.7, 105.6, 38, "重新渲染", "比赛或单场战报变化后重新生成整批文章；无输入变化时会复用当前版本。"),
      marker(272, 169.7, 300, 450.4, "文章信息", "核对渲染记录、完整状态、标题和生成时间。完整且仍为当前版本时可进入微信草稿候选。"),
      marker(592, 169.7, 792.8, 721.3, "最终效果", "右侧按批次顺序汇总已完赛比赛的战报素材，发布前应滚动检查每一场。"),
    ])],
  },
  {
    title: "创建微信公众号草稿",
    summary: "选择完整文章、确认顺序，再执行真实创建。",
    figures: [figure("admin-wechat-confirm", "微信草稿确认窗口显示前瞻任务自动关闭提示", "包含前瞻与战报的微信草稿最终确认", [
      marker(320, 338.2, 800, 45.8, "自动关闭提示", "只要选择中包含前瞻文章，成功创建或复用微信草稿后，对应前瞻批次的全部任务都会自动关闭；纯战报不会触发。"),
      marker(320, 402, 800, 73.6, "文章顺序", "第一篇是公众号头条，后续依次为次条。返回调整不会调用微信接口，也不会关闭任务。"),
      marker(1012.4, 596.6, 107.6, 38, "确认真实创建", "该按钮会真实调用微信公众号接口。只有微信成功且草稿记录保存后才关闭任务；失败时不会改变任务状态。"),
    ])],
  },
];

const userSteps: TutorialStep[] = [
  {
    title: "领取任务",
    summary: "在任务中心领取一场开放且尚未被其他成员认领的比赛。",
    figures: [figure("member-claim", "普通用户任务中心的待领取任务", "领取前：任务位于“待领取任务”", [
      marker(272, 309.6, 952.8, 330.3, "待领取任务", "这里只显示有效、开放且尚未被其他成员领取的比赛。列表为空时，通常表示暂时没有可领取任务。"),
      marker(313.6, 560.3, 104.6, 38, "领取任务", "点击后任务会立即绑定当前账号，并移动到“我的任务”；其他成员不能再领取同一场比赛。"),
    ])],
  },
  {
    title: "进入已领取的比赛",
    summary: "领取成功后，从“我的任务”进入写作页；也可以释放误领的任务。",
    figures: [figure("member-enter", "普通用户我的任务中的操作按钮", "领取后：在“我的任务”继续操作", [
      marker(1075, 300, 123, 29, "显示未开放任务", "批次任务自动关闭后，勾选这里仍可找到自己已领取的比赛并继续修改正文；关闭只禁止继续领取，不会清空内容。"),
      marker(308, 532, 57, 46, "释放", "仅在确实领错时使用。释放后认领关系会解除，开放中的任务会重新回到待领取列表，其他成员可以领取。"),
      marker(376.2, 536.4, 104.6, 38, "进入比赛", "打开比赛写作页面。进入页面本身不会保存任何修改，完成写作后仍需点击“保存正文”。"),
    ])],
  },
  {
    title: "填写并保存前瞻正文",
    summary: "在比赛页完成正文，明确保存成功后再离开。",
    figures: [
      figure("member-writing", "普通用户填写车辆对未央前瞻正文", "正文与署名（普通用户权限）", [
        marker(292, 285, 907, 49, "署名与保存状态", "署名来自任务配置，普通用户不能在这里更改。“已保存”表示当前正文已经写入服务器；“未保存”表示页面上仍有修改。"),
        marker(292, 369, 896, 343, "前瞻正文", "直接填写或粘贴内容；换行会在文章中形成段落。页面不会定时自动保存，刷新或关闭页面前务必手动保存。"),
      ]),
      figure("member-writing-actions", "普通用户写作页底部的撤销修改和保存正文按钮", "正文下方的保存操作（同一页面向下滚动）", [
        marker(993, 337, 81, 40, "撤销修改", "放弃页面上尚未保存的改动，恢复到服务器中的最近版本；当前没有改动时按钮不可用。"),
        marker(1080, 337, 108, 40, "保存正文", "把当前正文提交到服务器并增加保存序号。按钮变回不可用且页面显示“已保存”后，才表示保存完成。"),
      ]),
    ],
  },
];

const userExtras: TutorialStep[] = [
  {
    title: "查看前瞻批次",
    summary: "进入批次详情，查看比赛和管理员最近一次渲染的前瞻结果。",
    figures: [figure("member-previews", "普通用户前瞻批次列表中的进入按钮", "普通用户前瞻批次列表", [
      marker(1075, 324, 92, 62, "进入批次详情", "点击右箭头进入批次详情，可以查看批次内的比赛，并打开管理员最近一次渲染的前瞻文章。查看操作不会修改任何数据。", true),
    ])],
  },
  {
    title: "查看前瞻文章",
    summary: "阅读管理员最近一次渲染的公众号排版效果。",
    figures: [figure("member-preview-article", "普通用户查看男足前瞻文章", "普通用户看到的文章预览", [
      marker(272, 169.7, 300, 450.4, "文章信息", "左侧显示管理员最近一次渲染结果的标题、作者和生成时间。"),
      marker(592, 169.7, 632.8, 542.3, "文章预览", "右侧显示最近一次渲染的完整排版，可以滚动查看标题、图片和正文。"),
      marker(1094, 190, 114, 38, "全屏预览", "点击后使用更大的阅读区域查看同一份渲染结果，不会修改页面中的数据。"),
    ])],
  },
  {
    title: "查看战报",
    summary: "按批次查看单场战报或整批战报文章。",
    figures: [figure("member-reports", "普通用户展开战报批次并查看操作入口", "普通用户战报批次页面", [
      marker(288.8, 342.3, 98.4, 35, "展开批次", "点击日期展开或收起该批次的已完赛比赛。这个按钮只控制列表显示，不会查询赛事数据或重新渲染。"),
      marker(1075, 324, 92, 62, "预览批次文章", "点击右箭头打开管理员最近一次生成的整批战报文章；普通用户不能在这里重新生成批次文章。", true),
      marker(288.8, 404.7, 919.2, 105.6, "打开单场战报", "点击比赛卡进入单场页面，查看比分、事件和战报素材；单场页面允许按需重新渲染。"),
    ])],
  },
  {
    title: "渲染单场战报",
    summary: "普通用户也可按需刷新一场已完赛比赛的战报素材。",
    figures: [figure("member-report-match", "普通用户查看并重新渲染车辆对未央单场战报", "普通用户单场战报页面", [
      marker(986, 103.7, 116, 38, "返回战报批次", "返回上一层战报批次列表。该按钮不会撤销已经生成的单场战报。"),
      marker(1119.2, 103.7, 105.6, 38, "重新渲染", "重新查询这场比赛的数据并生成单场素材；会替换本页的旧渲染结果，但不会生成整批文章或微信草稿。"),
      marker(296.8, 261.2, 903.2, 450.8, "战报效果", "核对比分、阵容和事件；如页面显示诊断错误，应交由管理员检查比赛源数据。"),
    ])],
  },
];

export function TutorialFigure({ data }: { data: FigureData }) {
  return (
    <figure className="tutorial-figure">
      <div className="tutorial-figure__image-wrap" style={{ aspectRatio: `${data.imageWidth} / ${data.imageHeight}` }}>
        <img src={data.src} alt={data.alt} width={data.imageWidth} height={data.imageHeight} loading="lazy" />
        {data.annotations.map((annotation, index) => (
          <span
            aria-hidden="true"
            className={cx("tutorial-figure__marker", annotation.badgeRight && "tutorial-figure__marker--badge-right")}
            key={`${annotation.title}-${index}`}
            style={{
              left: `${annotation.x / data.imageWidth * 100}%`,
              top: `${annotation.y / data.imageHeight * 100}%`,
              width: `${annotation.width / data.imageWidth * 100}%`,
              height: `${annotation.height / data.imageHeight * 100}%`,
            }}
          ><b>{index + 1}</b></span>
        ))}
      </div>
      <figcaption>
        <strong>{data.caption}</strong>
        <ol>
          {data.annotations.map((annotation) => (
            <li key={annotation.title}>
              <span>{annotation.title}</span>
              <p>{annotation.description}</p>
            </li>
          ))}
        </ol>
      </figcaption>
    </figure>
  );
}

function StepList({ steps, start = 1 }: { steps: TutorialStep[]; start?: number }) {
  return (
    <div className="tutorial-step-list">
      {steps.map((step, index) => {
        const number = start + index;
        return (
          <article className="tutorial-step" id={`tutorial-step-${number}`} key={step.title}>
            <div className="tutorial-step__heading">
              <span>{String(number).padStart(2, "0")}</span>
              <div><h2>{step.title}</h2><p>{step.summary}</p></div>
            </div>
            <div className="tutorial-figures">{step.figures.map((item) => <TutorialFigure data={item} key={item.src} />)}</div>
            {step.note ? <div className="tutorial-note"><Info size={17} /><p>{step.note}</p></div> : null}
          </article>
        );
      })}
    </div>
  );
}

export function TutorialPage() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [section, setSection] = useState<"admin" | "user">(isAdmin ? "admin" : "user");
  useEffect(() => { document.title = "使用教程 · 绿茵宣传部"; }, []);
  const visibleSteps = section === "admin" ? adminSteps : [...userSteps, ...userExtras];

  return (
    <>
      <PageHeader eyebrow="快速上手" title="使用教程" description="截图来自当前网站真实页面；红色编号框与图片下方说明一一对应。" actions={<Badge tone={isAdmin ? "info" : "neutral"}>{isAdmin ? "管理员" : "普通用户"}</Badge>} />
      {isAdmin ? (
        <div className="tutorial-tabs" role="tablist" aria-label="教程角色">
          <button role="tab" aria-selected={section === "admin"} className={cx(section === "admin" && "is-active")} onClick={() => setSection("admin")}><Sparkles size={16} />管理员教程</button>
          <button role="tab" aria-selected={section === "user"} className={cx(section === "user" && "is-active")} onClick={() => setSection("user")}><BookOpen size={16} />普通用户教程</button>
        </div>
      ) : null}
      <div className="tutorial-layout">
        <aside className="tutorial-index">
          <Panel>
            <SectionTitle title="流程索引" description={`${visibleSteps.length} 个步骤`} />
            {visibleSteps.map((step, index) => <button type="button" onClick={() => document.getElementById(`tutorial-step-${index + 1}`)?.scrollIntoView()} key={step.title}><span>{index + 1}</span>{step.title}</button>)}
          </Panel>
        </aside>
        <main className="tutorial-content">
          <div className="tutorial-intro"><CheckCircle2 size={20} /><div><strong>{section === "admin" ? "管理员工作流" : "普通用户工作流"}</strong><span>{section === "admin" ? "从创建批次到生成微信公众号草稿。" : "领取任务，进入比赛，完成写作并保存。"}</span></div></div>
          {section === "admin" ? <StepList steps={adminSteps} /> : <><StepList steps={userSteps} /><section className="tutorial-extra"><div className="tutorial-extra__heading"><Eye size={20} /><div><h2>额外功能</h2><p>以下功能不属于写作主流程，可按需使用。</p></div></div><StepList steps={userExtras} start={userSteps.length + 1} /></section></>}
        </main>
      </div>
    </>
  );
}
