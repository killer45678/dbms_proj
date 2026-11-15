"""Database access layer for the Job Application Portal (PostgreSQL + psycopg2).

All user-supplied values are passed as psycopg2 parameters. Dynamic table and
column names are quoted with psycopg2.sql.Identifier and are selected only
from metadata for the public schema.
"""

from contextlib import contextmanager
from datetime import date, datetime

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor

from db_config import DB_SETTINGS

SCHEMA = "public"


@contextmanager
def connection():
    conn = psycopg2.connect(**DB_SETTINGS)
    try:
        yield conn
    finally:
        conn.close()


def test_connection():
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT current_database(), current_user, inet_server_addr()::text, "
                "inet_server_port(), version();"
            )
            return cur.fetchone()


def fetch_all(query, params=()):
    with connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]


def fetch_one(query, params=()):
    with connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            return dict(row) if row else None


def execute(query, params=()):
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            affected = cur.rowcount
            conn.commit()
            return affected


def get_tables():
    rows = fetch_all(
        """SELECT table_name
           FROM information_schema.tables
           WHERE table_schema = %s AND table_type = 'BASE TABLE'
           ORDER BY table_name;""",
        (SCHEMA,),
    )
    return [row["table_name"] for row in rows]


def table_exists(table_name):
    return table_name in get_tables()


def get_table_info(table_name):
    """Column metadata plus primary-key information for one public table."""
    query = """
        SELECT c.column_name, c.data_type, c.udt_name, c.is_nullable,
               c.column_default, c.is_identity, c.is_generated,
               c.character_maximum_length,
               CASE WHEN pk.column_name IS NULL THEN FALSE ELSE TRUE END AS is_pk
        FROM information_schema.columns AS c
        LEFT JOIN (
            SELECT kcu.table_schema, kcu.table_name, kcu.column_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
             AND tc.table_name = kcu.table_name
            WHERE tc.constraint_type = 'PRIMARY KEY'
              AND tc.table_schema = %s AND tc.table_name = %s
        ) AS pk
          ON pk.table_schema = c.table_schema
         AND pk.table_name = c.table_name
         AND pk.column_name = c.column_name
        WHERE c.table_schema = %s AND c.table_name = %s
        ORDER BY c.ordinal_position;
    """
    rows = fetch_all(query, (SCHEMA, table_name, SCHEMA, table_name))
    return [
        {
            "name": r["column_name"],
            "data_type": r["data_type"],
            "udt_name": r["udt_name"],
            "nullable": r["is_nullable"] == "YES",
            "default": r["column_default"],
            "identity": r["is_identity"] == "YES",
            "generated": r["is_generated"] != "NEVER",
            "max_length": r["character_maximum_length"],
            "primary_key": r["is_pk"],
        }
        for r in rows
    ]


def columns(table_name):
    return {c["name"].lower(): c["name"] for c in get_table_info(table_name)}


def pick_column(table_name, *candidates, required=False):
    available = columns(table_name)
    for candidate in candidates:
        actual = available.get(candidate.lower())
        if actual:
            return actual
    if required:
        raise ValueError(
            f"None of the expected columns {candidates} exists in table {table_name}. "
            f"Available columns: {', '.join(available.values())}"
        )
    return None


def _table_identifier(table_name):
    if table_name not in get_tables():
        raise ValueError(f"Table {table_name!r} is not in the public schema.")
    return sql.Identifier(SCHEMA, table_name)


def insert_row(table_name, values):
    """Insert a row using bound values and return the inserted row as a dict."""
    table_ident = _table_identifier(table_name)
    with connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if values:
                names = list(values.keys())
                query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
                    table_ident,
                    sql.SQL(", ").join(sql.Identifier(n) for n in names),
                    sql.SQL(", ").join(sql.Placeholder() for _ in names),
                )
                cur.execute(query, tuple(values[n] for n in names))
            else:
                query = sql.SQL("INSERT INTO {} DEFAULT VALUES RETURNING *").format(table_ident)
                cur.execute(query)
            row = dict(cur.fetchone())
            conn.commit()
            return row


def update_row(table_name, values, where_values):
    """Update rows using a parameterized WHERE clause; returns rowcount."""
    if not values:
        raise ValueError("No fields were supplied for the update.")
    if not where_values:
        raise ValueError("Update is blocked because no WHERE condition was supplied.")
    table_ident = _table_identifier(table_name)
    set_sql = sql.SQL(", ").join(
        sql.SQL("{} = %s").format(sql.Identifier(name)) for name in values
    )
    where_sql = sql.SQL(" AND ").join(
        sql.SQL("{} = %s").format(sql.Identifier(name)) for name in where_values
    )
    query = sql.SQL("UPDATE {} SET {} WHERE {}").format(table_ident, set_sql, where_sql)
    params = tuple(values.values()) + tuple(where_values.values())
    return execute(query, params)


def delete_row(table_name, where_values):
    if not where_values:
        raise ValueError("Delete is blocked because no WHERE condition was supplied.")
    table_ident = _table_identifier(table_name)
    where_sql = sql.SQL(" AND ").join(
        sql.SQL("{} = %s").format(sql.Identifier(name)) for name in where_values
    )
    query = sql.SQL("DELETE FROM {} WHERE {}").format(table_ident, where_sql)
    return execute(query, tuple(where_values.values()))


def get_employees():
    emp_id = pick_column("employee", "emp_id", required=True)
    name = pick_column("employee", "name", required=True)
    return fetch_all(
        sql.SQL("SELECT {id} AS emp_id, {name} AS name FROM {table} ORDER BY {name}").format(
            id=sql.Identifier(emp_id), name=sql.Identifier(name),
            table=_table_identifier("employee"),
        )
    )


def register_employee(name, resume="", experience="", phone_no=""):
    values = {pick_column("employee", "name", required=True): name.strip()}
    resume_col = pick_column("employee", "resume")
    experience_col = pick_column("employee", "experience")
    if resume_col:
        values[resume_col] = resume.strip() or None
    if experience_col:
        values[experience_col] = experience.strip() or None
    row = insert_row("employee", values)
    emp_id_col = pick_column("employee", "emp_id", required=True)
    emp_id = row[emp_id_col]
    if phone_no.strip() and table_exists("employee_phone"):
        phone_emp = pick_column("employee_phone", "emp_id", required=True)
        phone_col = pick_column("employee_phone", "phone_no", required=True)
        insert_row("employee_phone", {phone_emp: emp_id, phone_col: phone_no.strip()})
    return emp_id


def get_hr_managers():
    manager_id = pick_column("hr_manager", "employer_id", required=True)
    name = pick_column("hr_manager", "name", required=True)
    email = pick_column("hr_manager", "email")
    company_id = pick_column("hr_manager", "company_id")
    if company_id and table_exists("company"):
        company_pk = pick_column("company", "company_id", required=True)
        company_name = pick_column("company", "name", required=True)
        email_expr = sql.SQL("h.{}::text").format(sql.Identifier(email)) if email else sql.SQL("NULL::text")
        query = sql.SQL("""
            SELECT h.{mid} AS employer_id, h.{name} AS manager_name,
                   {email_expr} AS email, c.{company_name} AS company_name
            FROM {hr} h LEFT JOIN {co} c ON h.{company_id} = c.{company_pk}
            ORDER BY h.{name}
        """).format(
            mid=sql.Identifier(manager_id), name=sql.Identifier(name),
            email_expr=email_expr, hr=_table_identifier("hr_manager"),
            co=_table_identifier("company"), company_id=sql.Identifier(company_id),
            company_pk=sql.Identifier(company_pk), company_name=sql.Identifier(company_name),
        )
    else:
        email_expr = sql.SQL("{}::text").format(sql.Identifier(email)) if email else sql.SQL("NULL::text")
        query = sql.SQL("SELECT {} AS employer_id, {} AS manager_name, {} AS email, NULL::text AS company_name FROM {} ORDER BY {}").format(
            sql.Identifier(manager_id), sql.Identifier(name), email_expr,
            _table_identifier("hr_manager"), sql.Identifier(name),
        )
    return fetch_all(query)


def get_job_listings(search_text="", location="All", work_type="All", skill="All", manager_id=None):
    job_id = pick_column("job", "job_id", required=True)
    description = pick_column("job", "description", required=True)
    salary = pick_column("job", "expected_sal")
    loc = pick_column("job", "location")
    work = pick_column("job", "work_type")
    employer = pick_column("job", "employer_id")
    hr_id = pick_column("hr_manager", "employer_id") if table_exists("hr_manager") else None
    hr_company = pick_column("hr_manager", "company_id") if table_exists("hr_manager") else None
    company_id = pick_column("company", "company_id") if table_exists("company") else None
    company_name = pick_column("company", "name") if table_exists("company") else None
    manager_name = pick_column("hr_manager", "name") if table_exists("hr_manager") else None

    select_parts = [
        sql.SQL("j.{} AS job_id").format(sql.Identifier(job_id)),
        sql.SQL("j.{} AS description").format(sql.Identifier(description)),
        sql.SQL("j.{} AS expected_sal").format(sql.Identifier(salary)) if salary else sql.SQL("NULL AS expected_sal"),
        sql.SQL("j.{} AS location").format(sql.Identifier(loc)) if loc else sql.SQL("NULL::text AS location"),
        sql.SQL("j.{} AS work_type").format(sql.Identifier(work)) if work else sql.SQL("NULL::text AS work_type"),
        sql.SQL("j.{} AS employer_id").format(sql.Identifier(employer)) if employer else sql.SQL("NULL AS employer_id"),
    ]
    joins = sql.SQL("")
    if employer and hr_id and hr_company and company_id and company_name:
        select_parts.append(sql.SQL("c.{} AS company_name").format(sql.Identifier(company_name)))
        if manager_name:
            select_parts.append(sql.SQL("h.{} AS hr_name").format(sql.Identifier(manager_name)))
        else:
            select_parts.append(sql.SQL("NULL::text AS hr_name"))
        joins = sql.SQL(" LEFT JOIN {} h ON j.{} = h.{} LEFT JOIN {} c ON h.{} = c.{} ").format(
            _table_identifier("hr_manager"), sql.Identifier(employer), sql.Identifier(hr_id),
            _table_identifier("company"), sql.Identifier(hr_company), sql.Identifier(company_id),
        )
    else:
        select_parts.extend([sql.SQL("NULL::text AS company_name"), sql.SQL("NULL::text AS hr_name")])

    conditions = []
    params = []
    if search_text.strip():
        conditions.append(sql.SQL("j.{} ILIKE %s").format(sql.Identifier(description)))
        params.append(f"%{search_text.strip()}%")
    if location != "All" and loc:
        conditions.append(sql.SQL("j.{} ILIKE %s").format(sql.Identifier(loc)))
        params.append(f"%{location}%")
    if work_type != "All" and work:
        conditions.append(sql.SQL("j.{} = %s").format(sql.Identifier(work)))
        params.append(work_type)
    if manager_id is not None and employer:
        conditions.append(sql.SQL("j.{} = %s").format(sql.Identifier(employer)))
        params.append(manager_id)
    if skill != "All" and table_exists("job_skills") and table_exists("skills"):
        sk_id = pick_column("skills", "skill_id", required=True)
        sk_name = pick_column("skills", "skill_name", required=True)
        js_id = pick_column("job_skills", "skill_id", required=True)
        js_job = pick_column("job_skills", "job_id", required=True)
        conditions.append(sql.SQL("EXISTS (SELECT 1 FROM {} js JOIN {} s ON js.{} = s.{} WHERE js.{} = j.{} AND s.{} = %s)").format(
            _table_identifier("job_skills"), _table_identifier("skills"),
            sql.Identifier(js_id), sql.Identifier(sk_id), sql.Identifier(js_job),
            sql.Identifier(job_id), sql.Identifier(sk_name),
        ))
        params.append(skill)

    query = sql.SQL("SELECT {} FROM {} j {} {}").format(
        sql.SQL(", ").join(select_parts), _table_identifier("job"), joins,
        sql.SQL("WHERE ") + sql.SQL(" AND ").join(conditions) if conditions else sql.SQL(""),
    )
    query += sql.SQL(" ORDER BY j.{} DESC").format(sql.Identifier(job_id))
    return fetch_all(query, tuple(params))


def get_job_skills(job_id_value):
    if not all(table_exists(t) for t in ("skills", "job_skills")):
        return []
    sk_id = pick_column("skills", "skill_id", required=True)
    sk_name = pick_column("skills", "skill_name", required=True)
    js_id = pick_column("job_skills", "skill_id", required=True)
    js_job = pick_column("job_skills", "job_id", required=True)
    return fetch_all(
        sql.SQL("SELECT s.{} AS skill_name FROM {} js JOIN {} s ON js.{} = s.{} WHERE js.{} = %s ORDER BY s.{}").format(
            sql.Identifier(sk_name), _table_identifier("job_skills"), _table_identifier("skills"),
            sql.Identifier(js_id), sql.Identifier(sk_id), sql.Identifier(js_job), sql.Identifier(sk_name),
        ),
        (job_id_value,),
    )


def application_exists(emp_id, job_id_value):
    app_emp = pick_column("application", "emp_id", required=True)
    app_job = pick_column("application", "job_id", required=True)
    result = fetch_one(
        sql.SQL("SELECT 1 AS found FROM {} WHERE {} = %s AND {} = %s LIMIT 1").format(
            _table_identifier("application"), sql.Identifier(app_emp), sql.Identifier(app_job),
        ),
        (emp_id, job_id_value),
    )
    return result is not None


def apply_for_job(emp_id, job_id_value):
    if application_exists(emp_id, job_id_value):
        raise ValueError("You have already applied for this job.")
    values = {
        pick_column("application", "emp_id", required=True): emp_id,
        pick_column("application", "job_id", required=True): job_id_value,
    }
    status = pick_column("application", "status")
    submitted = pick_column("application", "date_of_submission", "date_submission")
    if status:
        values[status] = "Applied"
    if submitted:
        values[submitted] = date.today()
    return insert_row("application", values)


def get_applications_for_employee(emp_id):
    app_id = pick_column("application", "application_id", required=True)
    app_emp = pick_column("application", "emp_id", required=True)
    app_job = pick_column("application", "job_id", required=True)
    app_status = pick_column("application", "status")
    app_date = pick_column("application", "date_of_submission", "date_submission")
    job_id = pick_column("job", "job_id", required=True)
    job_desc = pick_column("job", "description", required=True)
    job_loc = pick_column("job", "location")
    job_work = pick_column("job", "work_type")
    job_emp = pick_column("job", "employer_id")
    salary = pick_column("job", "expected_sal")
    select = [
        sql.SQL("a.{} AS application_id").format(sql.Identifier(app_id)),
        sql.SQL("a.{} AS status").format(sql.Identifier(app_status)) if app_status else sql.SQL("NULL::text AS status"),
        sql.SQL("a.{} AS date_of_submission").format(sql.Identifier(app_date)) if app_date else sql.SQL("NULL AS date_of_submission"),
        sql.SQL("j.{} AS description").format(sql.Identifier(job_desc)),
        sql.SQL("j.{} AS location").format(sql.Identifier(job_loc)) if job_loc else sql.SQL("NULL::text AS location"),
        sql.SQL("j.{} AS work_type").format(sql.Identifier(job_work)) if job_work else sql.SQL("NULL::text AS work_type"),
        sql.SQL("j.{} AS expected_sal").format(sql.Identifier(salary)) if salary else sql.SQL("NULL AS expected_sal"),
    ]
    joins = sql.SQL("")
    if job_emp and table_exists("hr_manager") and table_exists("company"):
        hr_id = pick_column("hr_manager", "employer_id", required=True)
        hr_co = pick_column("hr_manager", "company_id", required=True)
        co_id = pick_column("company", "company_id", required=True)
        co_name = pick_column("company", "name", required=True)
        select.append(sql.SQL("c.{} AS company_name").format(sql.Identifier(co_name)))
        joins = sql.SQL(" LEFT JOIN {} h ON j.{} = h.{} LEFT JOIN {} c ON h.{} = c.{} ").format(
            _table_identifier("hr_manager"), sql.Identifier(job_emp), sql.Identifier(hr_id),
            _table_identifier("company"), sql.Identifier(hr_co), sql.Identifier(co_id),
        )
    else:
        select.append(sql.SQL("NULL::text AS company_name"))
    query = sql.SQL("SELECT {} FROM {} a JOIN {} j ON a.{} = j.{} {} WHERE a.{} = %s ORDER BY a.{} DESC").format(
        sql.SQL(", ").join(select), _table_identifier("application"), _table_identifier("job"),
        sql.Identifier(app_job), sql.Identifier(job_id), joins, sql.Identifier(app_emp), sql.Identifier(app_id),
    )
    return fetch_all(query, (emp_id,))


def get_applications_for_manager(manager_id):
    app_id = pick_column("application", "application_id", required=True)
    app_emp = pick_column("application", "emp_id", required=True)
    app_job = pick_column("application", "job_id", required=True)
    app_status = pick_column("application", "status")
    app_date = pick_column("application", "date_of_submission", "date_submission")
    emp_id = pick_column("employee", "emp_id", required=True)
    emp_name = pick_column("employee", "name", required=True)
    job_id = pick_column("job", "job_id", required=True)
    job_desc = pick_column("job", "description", required=True)
    job_manager = pick_column("job", "employer_id", required=True)
    select = [
        sql.SQL("a.{} AS application_id").format(sql.Identifier(app_id)),
        sql.SQL("a.{} AS status").format(sql.Identifier(app_status)) if app_status else sql.SQL("NULL::text AS status"),
        sql.SQL("a.{} AS date_of_submission").format(sql.Identifier(app_date)) if app_date else sql.SQL("NULL AS date_of_submission"),
        sql.SQL("e.{} AS employee_name").format(sql.Identifier(emp_name)),
        sql.SQL("e.{} AS emp_id").format(sql.Identifier(emp_id)),
        sql.SQL("j.{} AS description").format(sql.Identifier(job_desc)),
        sql.SQL("j.{} AS job_id").format(sql.Identifier(job_id)),
    ]
    query = sql.SQL("SELECT {} FROM {} a JOIN {} e ON a.{} = e.{} JOIN {} j ON a.{} = j.{} WHERE j.{} = %s ORDER BY a.{} DESC").format(
        sql.SQL(", ").join(select), _table_identifier("application"), _table_identifier("employee"),
        sql.Identifier(app_emp), sql.Identifier(emp_id), _table_identifier("job"),
        sql.Identifier(app_job), sql.Identifier(job_id), sql.Identifier(job_manager), sql.Identifier(app_id),
    )
    return fetch_all(query, (manager_id,))


def get_employee_skills(emp_id):
    if not all(table_exists(t) for t in ("employee_skills", "skills")):
        return []
    es_emp = pick_column("employee_skills", "emp_id", required=True)
    es_skill = pick_column("employee_skills", "skill_id", required=True)
    sk_id = pick_column("skills", "skill_id", required=True)
    sk_name = pick_column("skills", "skill_name", required=True)
    return fetch_all(
        sql.SQL("SELECT s.{} AS skill_name, s.{} AS skill_id FROM {} es JOIN {} s ON es.{} = s.{} WHERE es.{} = %s ORDER BY s.{}").format(
            sql.Identifier(sk_name), sql.Identifier(sk_id), _table_identifier("employee_skills"),
            _table_identifier("skills"), sql.Identifier(es_skill), sql.Identifier(sk_id),
            sql.Identifier(es_emp), sql.Identifier(sk_name),
        ),
        (emp_id,),
    )


def get_skills():
    if not table_exists("skills"):
        return []
    sk_id = pick_column("skills", "skill_id", required=True)
    sk_name = pick_column("skills", "skill_name", required=True)
    return fetch_all(
        sql.SQL("SELECT {} AS skill_id, {} AS skill_name FROM {} ORDER BY {}").format(
            sql.Identifier(sk_id), sql.Identifier(sk_name), _table_identifier("skills"), sql.Identifier(sk_name)
        )
    )


def add_employee_skill(emp_id, skill_id):
    emp_col = pick_column("employee_skills", "emp_id", required=True)
    skill_col = pick_column("employee_skills", "skill_id", required=True)
    exists = fetch_one(
        sql.SQL("SELECT 1 AS found FROM {} WHERE {} = %s AND {} = %s").format(
            _table_identifier("employee_skills"), sql.Identifier(emp_col), sql.Identifier(skill_col)
        ),
        (emp_id, skill_id),
    )
    if exists:
        return 0
    insert_row("employee_skills", {emp_col: emp_id, skill_col: skill_id})
    return 1


def add_job_skill(job_id_value, skill_id):
    job_col = pick_column("job_skills", "job_id", required=True)
    skill_col = pick_column("job_skills", "skill_id", required=True)
    exists = fetch_one(
        sql.SQL("SELECT 1 AS found FROM {} WHERE {} = %s AND {} = %s").format(
            _table_identifier("job_skills"), sql.Identifier(job_col), sql.Identifier(skill_col)
        ),
        (job_id_value, skill_id),
    )
    if exists:
        return 0
    insert_row("job_skills", {job_col: job_id_value, skill_col: skill_id})
    return 1


def get_employee_phones(emp_id):
    if not table_exists("employee_phone"):
        return []
    emp_col = pick_column("employee_phone", "emp_id", required=True)
    phone_col = pick_column("employee_phone", "phone_no", required=True)
    return fetch_all(
        sql.SQL("SELECT {} AS phone_no FROM {} WHERE {} = %s ORDER BY {} ").format(
            sql.Identifier(phone_col), _table_identifier("employee_phone"),
            sql.Identifier(emp_col), sql.Identifier(phone_col),
        ),
        (emp_id,),
    )


def add_employee_phone(emp_id, phone):
    emp_col = pick_column("employee_phone", "emp_id", required=True)
    phone_col = pick_column("employee_phone", "phone_no", required=True)
    insert_row("employee_phone", {emp_col: emp_id, phone_col: phone.strip()})


def withdraw_application(application_id, emp_id):
    """Delete only the selected applicant's application row (explicit WHERE)."""
    app_id_col = pick_column("application", "application_id", required=True)
    emp_col = pick_column("application", "emp_id", required=True)
    return delete_row("application", {app_id_col: application_id, emp_col: emp_id})


def update_application_status(application_id, new_status):
    app_id = pick_column("application", "application_id", required=True)
    status = pick_column("application", "status", required=True)
    return update_row("application", {status: new_status}, {app_id: application_id})


def get_interviews_for_application_ids(application_ids):
    if not table_exists("interview") or not application_ids:
        return []
    app_col = pick_column("interview", "application_id")
    if not app_col:
        return []
    schedule_col = pick_column("interview", "scheduled_datetime", "scheduled_time", "scheduled_at")
    feedback_col = pick_column("interview", "feedbackfromHR", "feedback_from_hr", "feedback")
    i_id = pick_column("interview", "id", "interview_id")
    select = [
        sql.SQL("{} AS application_id").format(sql.Identifier(app_col)),
        sql.SQL("{} AS scheduled_at").format(sql.Identifier(schedule_col)) if schedule_col else sql.SQL("NULL AS scheduled_at"),
        sql.SQL("{} AS feedback").format(sql.Identifier(feedback_col)) if feedback_col else sql.SQL("NULL::text AS feedback"),
        sql.SQL("{} AS interview_id").format(sql.Identifier(i_id)) if i_id else sql.SQL("NULL AS interview_id"),
    ]
    query = sql.SQL("SELECT {} FROM {} WHERE {} = ANY(%s) ORDER BY {} DESC").format(
        sql.SQL(", ").join(select), _table_identifier("interview"), sql.Identifier(app_col),
        sql.Identifier(schedule_col) if schedule_col else sql.Identifier(app_col),
    )
    return fetch_all(query, (list(application_ids),))


def get_employee_offers(emp_id):
    if not table_exists("job_offer"):
        return []
    offer_app = pick_column("job_offer", "application_id")
    offer_interview = pick_column("job_offer", "interview_id")
    if offer_app:
        app_emp = pick_column("application", "emp_id", required=True)
        app_id = pick_column("application", "application_id", required=True)
        return fetch_all(
            sql.SQL("SELECT o.* FROM {} o JOIN {} a ON o.{} = a.{} WHERE a.{} = %s").format(
                _table_identifier("job_offer"), _table_identifier("application"),
                sql.Identifier(offer_app), sql.Identifier(app_id), sql.Identifier(app_emp),
            ),
            (emp_id,),
        )
    if offer_interview and table_exists("interview"):
        int_id = pick_column("interview", "id", "interview_id", required=True)
        int_app = pick_column("interview", "application_id", required=True)
        app_id = pick_column("application", "application_id", required=True)
        app_emp = pick_column("application", "emp_id", required=True)
        return fetch_all(
            sql.SQL("SELECT o.* FROM {} o JOIN {} i ON o.{} = i.{} JOIN {} a ON i.{} = a.{} WHERE a.{} = %s").format(
                _table_identifier("job_offer"), _table_identifier("interview"),
                sql.Identifier(offer_interview), sql.Identifier(int_id), _table_identifier("application"),
                sql.Identifier(int_app), sql.Identifier(app_id), sql.Identifier(app_emp),
            ),
            (emp_id,),
        )
    return []


def create_interview(application_id, scheduled_at, manager_id=None, feedback=""):
    values = {}
    app_col = pick_column("interview", "application_id", required=True)
    schedule_col = pick_column("interview", "scheduled_datetime", "scheduled_time", "scheduled_at", required=True)
    values[app_col] = application_id
    values[schedule_col] = scheduled_at
    manager_col = pick_column("interview", "employer_id", "hr_manager_id", "manager_id")
    if manager_col and manager_id is not None:
        values[manager_col] = manager_id
    feedback_col = pick_column("interview", "feedbackfromHR", "feedback_from_hr", "feedback")
    if feedback_col and feedback.strip():
        values[feedback_col] = feedback.strip()
    return insert_row("interview", values)


def create_job_offer(application_id, manager_id, status, letter):
    values = {}
    app_col = pick_column("job_offer", "application_id")
    interview_col = pick_column("job_offer", "interview_id")
    if app_col:
        values[app_col] = application_id
    elif interview_col:
        interview_id_col = pick_column("interview", "id", "interview_id", required=True)
        interview = fetch_one(
            sql.SQL("SELECT {} AS id FROM {} WHERE {} = %s ORDER BY {} DESC LIMIT 1").format(
                sql.Identifier(interview_id_col), _table_identifier("interview"),
                sql.Identifier(pick_column("interview", "application_id", required=True)),
                sql.Identifier(interview_id_col),
            ),
            (application_id,),
        )
        if not interview:
            raise ValueError("Schedule an interview for this application before creating its offer.")
        values[interview_col] = interview["id"]
    else:
        raise ValueError("The job_offer table has no application_id or interview_id link column.")
    status_col = pick_column("job_offer", "status", required=True)
    letter_col = pick_column("job_offer", "letter")
    values[status_col] = status
    if letter_col:
        values[letter_col] = letter
    manager_col = pick_column("job_offer", "employer_id", "hr_manager_id", "manager_id")
    if manager_col and manager_id is not None:
        values[manager_col] = manager_id
    return insert_row("job_offer", values)


def add_job_for_manager(manager_id, description, expected_sal, location, work_type):
    values = {
        pick_column("job", "description", required=True): description.strip(),
        pick_column("job", "location", required=True): location.strip(),
        pick_column("job", "work_type", required=True): work_type,
        pick_column("job", "employer_id", required=True): manager_id,
    }
    sal_col = pick_column("job", "expected_sal")
    if sal_col:
        values[sal_col] = expected_sal
    return insert_row("job", values)


def get_employee_profile(emp_id):
    emp_pk = pick_column("employee", "emp_id", required=True)
    return fetch_one(
        sql.SQL("SELECT * FROM {} WHERE {} = %s").format(_table_identifier("employee"), sql.Identifier(emp_pk)),
        (emp_id,),
    )


def update_employee_profile(emp_id, name, resume, experience):
    values = {}
    for candidates, value in [
        (("name",), name.strip()), (("resume",), resume.strip() or None),
        (("experience",), experience.strip() or None),
    ]:
        col_name = pick_column("employee", *candidates)
        if col_name:
            values[col_name] = value
    emp_pk = pick_column("employee", "emp_id", required=True)
    return update_row("employee", values, {emp_pk: emp_id})


def get_hr_applications(manager_id):
    return get_applications_for_manager(manager_id)


def get_reports():
    """Report names and SQL templates based on the team's Lab 10A query sheet.

    These are HR/admin reporting queries, not applicant-facing actions.
    SQL values use %s placeholders. The report page exposes parameters separately.
    """
    reports = {
        "Accepted job offers": ("SELECT * FROM job_offer WHERE status = %s", ("Accepted",)),
        "Companies with more than one HR manager": (
            "SELECT c.name AS company, COUNT(*) AS hr_managers "
            "FROM company c JOIN hr_manager h ON c.company_id = h.company_id "
            "GROUP BY c.name HAVING COUNT(*) > 1 ORDER BY c.name", ()),
        "C++ skilled applicants and resumes": (
            "SELECT DISTINCT e.emp_id, e.name, e.resume FROM employee e "
            "JOIN employee_skills es ON e.emp_id = es.emp_id "
            "JOIN skills s ON es.skill_id = s.skill_id WHERE s.skill_name ILIKE %s", ("%C++%",)),
        "Withdrawn job offers": ("SELECT * FROM job_offer WHERE status = %s", ("Withdrawn",)),
        "Remote jobs": ("SELECT * FROM job WHERE work_type = %s ORDER BY job_id DESC", ("Remote",)),
        "Shortlisted applications": ("SELECT * FROM application WHERE status = %s ORDER BY application_id DESC", ("Shortlisted",)),
        "Jobs in Indore": ("SELECT * FROM job WHERE location = %s ORDER BY job_id DESC", ("Indore",)),
        "Indore or remote jobs": ("SELECT * FROM job WHERE location = %s OR work_type = %s ORDER BY job_id DESC", ("Indore", "Remote")),
        "Hired applications": ("SELECT * FROM application WHERE status = %s ORDER BY application_id DESC", ("Hired",)),
        "Employee names and phone numbers": (
            "SELECT e.name, p.phone_no FROM employee e JOIN employee_phone p ON e.emp_id = p.emp_id ORDER BY e.name", ()),
        "Jobs mentioning ML / Data Analyst": (
            "SELECT * FROM job WHERE description ILIKE %s OR description ILIKE %s ORDER BY job_id DESC", ("%ML%", "%Data_Analyst%")),
        "Jobs requiring C++": (
            "SELECT DISTINCT j.* FROM job j JOIN job_skills js ON j.job_id = js.job_id "
            "JOIN skills s ON js.skill_id = s.skill_id WHERE s.skill_name ILIKE %s", ("%C++%",)),
        "Average expected salary": ("SELECT AVG(expected_sal) AS average_expected_salary FROM job", ()),
        "Jobs above average expected salary": (
            "SELECT * FROM job WHERE expected_sal > (SELECT AVG(expected_sal) FROM job) ORDER BY expected_sal DESC", ()),
        "Companies offering the maximum salary": (
            "SELECT DISTINCT c.name FROM company c JOIN hr_manager h ON c.company_id = h.company_id "
            "JOIN job j ON j.employer_id = h.employer_id "
            "WHERE j.expected_sal = (SELECT MAX(expected_sal) FROM job)", ()),
        "Companies offering above-average salaries": (
            "SELECT DISTINCT c.name FROM company c JOIN hr_manager h ON c.company_id = h.company_id "
            "JOIN job j ON j.employer_id = h.employer_id "
            "WHERE j.expected_sal > (SELECT AVG(expected_sal) FROM job)", ()),
        "Companies with remote work requirements": (
            "SELECT DISTINCT c.name FROM company c JOIN hr_manager h ON c.company_id = h.company_id "
            "JOIN job j ON j.employer_id = h.employer_id WHERE j.work_type = %s", ("Remote",)),
        "Companies with jobs located remotely": (
            "SELECT DISTINCT c.name FROM company c JOIN hr_manager h ON c.company_id = h.company_id "
            "JOIN job j ON j.employer_id = h.employer_id WHERE j.location = %s", ("Remote",)),
        "Job and applicant skills that match": (
            "SELECT j.job_id, j.description, e.emp_id, e.name AS employee_name, s.skill_name "
            "FROM job j JOIN job_skills js ON j.job_id = js.job_id "
            "JOIN skills s ON js.skill_id = s.skill_id "
            "JOIN employee_skills es ON es.skill_id = s.skill_id "
            "JOIN employee e ON e.emp_id = es.emp_id ORDER BY j.job_id, e.name", ()),
        "Skills of hired applicants for a selected job": (
            "SELECT DISTINCT j.job_id, j.description, e.name AS employee_name, s.skill_name "
            "FROM application a JOIN job j ON a.job_id = j.job_id "
            "JOIN employee e ON a.emp_id = e.emp_id "
            "JOIN employee_skills es ON e.emp_id = es.emp_id "
            "JOIN skills s ON es.skill_id = s.skill_id "
            "WHERE a.status = %s AND j.job_id = %s ORDER BY e.name, s.skill_name", ("Hired",)),
        "Rejected applications": ("SELECT * FROM application WHERE status = %s ORDER BY application_id DESC", ("Rejected",)),
        "Application status by employee": (
            "SELECT e.name, a.status FROM employee e JOIN application a ON a.emp_id = e.emp_id ORDER BY e.name", ()),
        "Number of hired applicants with an interview": (
            "SELECT COUNT(DISTINCT a.application_id) AS hired_after_interview "
            "FROM application a JOIN interview i ON i.application_id = a.application_id WHERE a.status = %s", ("Hired",)),
        "Number of shortlisted applicants with an interview": (
            "SELECT COUNT(DISTINCT a.application_id) AS shortlisted_after_interview "
            "FROM application a JOIN interview i ON i.application_id = a.application_id WHERE a.status = %s", ("Shortlisted",)),
        "Applications submitted on or before 1 July 2026": (
            "SELECT * FROM application WHERE date_of_submission <= %s ORDER BY date_of_submission DESC", (date(2026, 7, 1),)),
        "Companies whose profile mentions Noida": (
            "SELECT * FROM company WHERE profile ILIKE %s", ("%Noida%",)),
        "Employee names and their skills": (
            "SELECT e.name, s.skill_name FROM employee e JOIN employee_skills es ON e.emp_id = es.emp_id "
            "JOIN skills s ON es.skill_id = s.skill_id ORDER BY e.name", ()),
        "Jobs and their required skills": (
            "SELECT j.description, s.skill_name FROM job j JOIN job_skills js ON j.job_id = js.job_id "
            "JOIN skills s ON js.skill_id = s.skill_id ORDER BY j.job_id", ()),
    }

    # The ERD and the SQL handout use different labels for these interview fields.
    # Resolve the real column names from information_schema before building the SQL.
    if table_exists("interview"):
        schedule_col = pick_column("interview", "scheduled_datetime", "scheduled_time", "scheduled_at")
        if schedule_col:
            safe_schedule = '"' + schedule_col.replace('"', '""') + '"'
            reports["Interviews after 10 July 2026, 11:00 AM"] = (
                f"SELECT * FROM interview WHERE {safe_schedule} > %s ORDER BY {safe_schedule}",
                (datetime(2026, 7, 10, 11, 0),),
            )
        feedback_col = pick_column("interview", "feedback", "feedbackfromHR", "feedback_from_hr")
        if feedback_col:
            safe_feedback = '"' + feedback_col.replace('"', '""') + '"'
            reports["Interviews with positive HR feedback"] = (
                f"SELECT * FROM interview WHERE {safe_feedback} ILIKE %s", ("%Positive%",)
            )

    return reports


def run_report(report_name, extra_params=()):
    reports = get_reports()
    query, base_params = reports[report_name]
    return fetch_all(query, tuple(base_params) + tuple(extra_params))
