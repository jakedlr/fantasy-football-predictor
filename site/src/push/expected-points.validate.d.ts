// Types for the generated expected-points.validate.js (scripts/build_validator.mjs).
export interface ValidationError {
  instancePath: string;
  schemaPath: string;
  keyword: string;
  params: Record<string, unknown>;
  message?: string;
}

export interface ValidateFunction {
  (data: unknown): boolean;
  /** The first error of the last failed call (allErrors is off); null after a pass. */
  errors?: ValidationError[] | null;
}

declare const validate: ValidateFunction;
export { validate };
export default validate;
