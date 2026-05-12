import clsx from "clsx";

export default function Card({ children, className = "", ...props }) {
  return (
    <div
      className={clsx(
        "glass rounded-2xl p-5 animate-fade-in transition-all duration-200",
        className
      )}
      {...props}
    >
      {children}
    </div>
  );
}
