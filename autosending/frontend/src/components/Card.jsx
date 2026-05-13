import clsx from "clsx";

export default function Card({ children, className = "", noHover = false, ...props }) {
  return (
    <div
      className={clsx(
        "card animate-fade-in",
        noHover && "card-no-hover",
        className
      )}
      {...props}
    >
      <div className="card-body">{children}</div>
    </div>
  );
}
