import type { ButtonHTMLAttributes, HTMLAttributes, InputHTMLAttributes, ReactNode } from "react";
import { AlertCircle, CheckCircle2, LoaderCircle, X, XCircle } from "lucide-react";

export function cx(...values: Array<string | false | null | undefined>): string {
  return values.filter(Boolean).join(" ");
}

type ButtonVariant = "primary" | "secondary" | "quiet" | "danger";

export function Button({
  children,
  variant = "secondary",
  loading = false,
  className,
  disabled,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  loading?: boolean;
}) {
  return (
    <button
      className={cx("button", `button--${variant}`, className)}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? <LoaderCircle className="spin" size={16} aria-hidden /> : null}
      {children}
    </button>
  );
}

export function Panel({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <section className={cx("panel", className)} {...props} />;
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
        <h1>{title}</h1>
        {description ? <p className="page-description">{description}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: string }) {
  return <span className={cx("badge", `badge--${tone}`)}>{children}</span>;
}

export function Alert({
  children,
  tone = "info",
  onDismiss,
}: {
  children: ReactNode;
  tone?: "info" | "success" | "warning" | "danger";
  onDismiss?: () => void;
}) {
  const Icon = tone === "success" ? CheckCircle2 : tone === "danger" ? XCircle : AlertCircle;
  return (
    <div className={cx("alert", `alert--${tone}`)} role={tone === "danger" ? "alert" : "status"}>
      <Icon size={18} aria-hidden />
      <div className="alert__content">{children}</div>
      {onDismiss ? (
        <button className="icon-button" onClick={onDismiss} aria-label="关闭提示">
          <X size={16} />
        </button>
      ) : null}
    </div>
  );
}

export function Field({
  label,
  hint,
  children,
  htmlFor,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  htmlFor?: string;
}) {
  return (
    <div className="field">
      <label htmlFor={htmlFor}>{label}</label>
      {children}
      {hint ? <span className="field__hint">{hint}</span> : null}
    </div>
  );
}

export function NameInput({ value, onChange, ...props }: Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange"> & { value: string; onChange: (value: string) => void }) {
  return (
    <input
      {...props}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      placeholder="多人用顿号或逗号分隔"
    />
  );
}

export function EmptyState({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      {description ? <p>{description}</p> : null}
      {action}
    </div>
  );
}

export function LoadingScreen({ label = "正在加载" }: { label?: string }) {
  return (
    <div className="loading-screen" role="status">
      <LoaderCircle className="spin" size={22} />
      <span>{label}</span>
    </div>
  );
}

export function Modal({
  title,
  children,
  actions,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  actions: ReactNode;
  onClose?: () => void;
  wide?: boolean;
}) {
  return (
    <div className="modal-backdrop" role="presentation">
      <div className={cx("modal", wide && "modal--wide")} role="dialog" aria-modal="true" aria-labelledby="modal-title">
        <div className="modal__header">
          <h2 id="modal-title">{title}</h2>
          {onClose ? (
            <button className="icon-button" onClick={onClose} aria-label="关闭">
              <X size={18} />
            </button>
          ) : null}
        </div>
        <div className="modal__body">{children}</div>
        <div className="modal__actions">{actions}</div>
      </div>
    </div>
  );
}

export function SectionTitle({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <div className="section-title">
      <div>
        <h2>{title}</h2>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="section-actions">{actions}</div> : null}
    </div>
  );
}
