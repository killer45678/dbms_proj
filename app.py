"""Streamlit front end for the team's ERD-based Job Application Portal.

The app provides applicant and HR workspaces. The database schema is read
from PostgreSQL at runtime; it does not create or alter the user's tables.
"""

from datetime import date, datetime, time

import pandas as pd
import streamlit as st

from db import (
    add_employee_phone,
    add_employee_skill,
    add_job_for_manager,
    add_job_skill,
    apply_for_job,
    create_interview,
    create_job_offer,
    get_applications_for_employee,
    get_applications_for_manager,
    get_employee_offers,
    get_employee_phones,
    get_employee_profile,
    get_employee_skills,
    get_employees,
    get_hr_managers,
    get_interviews_for_application_ids,
    get_job_listings,
    get_job_skills,
    get_reports,
    get_skills,
    pick_column,
    register_employee,
    run_report,
    table_exists,
    test_connection,
    update_application_status,
    update_employee_profile,
    withdraw_application,
)


st.set_page_config(
    page_title="CareerConnect | Job Portal",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      .block-container {padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1450px;}
      [data-testid="stSidebar"] {border-right: 1px solid rgba(128,128,128,.18);}
      .hero {padding: 1.35rem 1.55rem; border-radius: 18px;
             background: linear-gradient(115deg, #10243a 0%, #153d55 55%, #196b69 100%);
             color: #f4fbff; margin-bottom: 1.15rem;}
      .hero h1 {font-size: 2rem; margin: 0 0 .35rem 0; color: #ffffff;}
      .hero p {margin: 0; color: #d7e8ee; font-size: 1rem;}
      .muted {color: #718096; font-size: .9rem;}
      .job-title {font-size: 1.15rem; font-weight: 700; margin-bottom: .25rem;}
      .pill {display: inline-block; border: 1px solid rgba(120,140,160,.35);
             border-radius: 20px; padding: .16rem .58rem; margin: .12rem .2rem .12rem 0;
             font-size: .78rem;}
      div[data-testid="stMetric"] {background: rgba(120,140,160,.08); padding: .7rem .85rem; border-radius: 12px;}
    </style>
    """,
    unsafe_allow_html=True,
)


def hero(title, subtitle):
    st.markdown(
        f'<div class="hero"><h1>{title}</h1><p>{subtitle}</p></div>',
        unsafe_allow_html=True,
    )


def df_or_empty(rows, message="Nothing to display yet."):
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info(message)


def status_label(value):
    text = str(value or "Not set")
    if text.lower() in {"hired", "accepted", "shortlisted"}:
        st.success(text)
    elif text.lower() in {"rejected", "withdrawn"}:
        st.error(text)
    else:
        st.info(text)


def show_connection():
    try:
        info = test_connection()
        st.sidebar.success(f"Connected · {info[0]}")
        return True
    except Exception as exc:
        st.error("Could not connect to PostgreSQL. Check db_config.py and make sure PostgreSQL is running.")
        st.exception(exc)
        st.stop()


def applicant_registration():
    st.subheader("Create your applicant profile")
    st.caption("This saves your profile in the employee table in your project database.")
    with st.form("applicant_register_form"):
        name = st.text_input("Full name *")
        phone = st.text_input("Phone number")
        experience = st.text_area("Experience", placeholder="Fresher, 2 years in Python, internship details…")
        resume = st.text_area("Resume / profile summary", placeholder="Education, projects, certifications and achievements…")
        submit = st.form_submit_button("Create applicant profile", type="primary")
    if submit:
        if not name.strip():
            st.error("Please enter your name.")
        else:
            try:
                new_id = register_employee(name, resume, experience, phone)
                st.success(f"Profile created successfully. Your applicant ID is {new_id}.")
                st.rerun()
            except Exception as exc:
                st.error("Could not create your profile. Check the table constraints and try again.")
                st.exception(exc)


def applicant_workspace():
    hero("Find work that moves you forward", "Explore opportunities, apply with your profile, and follow every stage of your application.")
    if not table_exists("employee") or not table_exists("job") or not table_exists("application"):
        st.error("The database must contain employee, job and application tables to use the applicant portal.")
        return

    employees = get_employees()
    if not employees:
        applicant_registration()
        return

    st.sidebar.markdown("### Applicant profile")
    selected_emp = st.sidebar.selectbox(
        "Continue as",
        employees,
        format_func=lambda row: f"{row['name']}  ·  ID {row['emp_id']}",
        key="applicant_identity",
    )
    emp_id = selected_emp["emp_id"]
    emp_name = selected_emp["name"]
    st.caption(f"Signed in for this demo as **{emp_name}** · Applicant ID: `{emp_id}`")
    with st.expander("New here? Register another applicant profile"):
        with st.form("register_another_applicant"):
            new_name = st.text_input("Full name")
            new_phone = st.text_input("Phone number")
            new_experience = st.text_area("Experience")
            new_resume = st.text_area("Resume / profile summary")
            register_another = st.form_submit_button("Register applicant")
        if register_another:
            if not new_name.strip():
                st.error("Please enter a name.")
            else:
                try:
                    created_id = register_employee(new_name, new_resume, new_experience, new_phone)
                    st.success(f"Applicant profile created. ID: {created_id}. Select it from the sidebar.")
                    st.rerun()
                except Exception as exc:
                    st.error("Could not create the applicant profile.")
                    st.exception(exc)

    tab_jobs, tab_apps, tab_profile = st.tabs(["Explore jobs", "My applications", "My profile"])

    with tab_jobs:
        st.subheader("Explore open opportunities")
        all_jobs = get_job_listings()
        work_types = sorted({str(j.get("work_type")) for j in all_jobs if j.get("work_type")})
        skill_rows = get_skills() if table_exists("skills") else []
        skill_names = [row["skill_name"] for row in skill_rows]

        f1, f2, f3, f4 = st.columns([2.1, 1.2, 1.2, 1.4])
        with f1:
            search = st.text_input("Search roles", placeholder="e.g. Python developer, analyst…")
        with f2:
            location = st.text_input("Location", placeholder="Any location")
        with f3:
            work_choice = st.selectbox("Work mode", ["All"] + work_types)
        with f4:
            skill_choice = st.selectbox("Required skill", ["All"] + skill_names)

        jobs = get_job_listings(
            search_text=search,
            location=location.strip() if location.strip() else "All",
            work_type=work_choice,
            skill=skill_choice,
        )
        st.caption(f"{len(jobs)} matching position(s)")

        if not jobs:
            st.info("No matching jobs found. Try changing your search or filters.")
        for job in jobs:
            with st.container(border=True):
                left, right = st.columns([5, 1.25])
                with left:
                    st.markdown(f'<div class="job-title">{job.get("description") or "Position"}</div>', unsafe_allow_html=True)
                    st.write(f"**{job.get('company_name') or 'Company not listed'}**  ·  {job.get('location') or 'Location not specified'}")
                    meta = []
                    if job.get("work_type"):
                        meta.append(str(job["work_type"]))
                    if job.get("expected_sal") is not None:
                        meta.append(f"Expected salary: {job['expected_sal']}")
                    if job.get("employer_id") is not None:
                        meta.append(f"Employer ID: {job['employer_id']}")
                    st.caption("  |  ".join(meta))
                    job_skills = [r["skill_name"] for r in get_job_skills(job["job_id"])]
                    if job_skills:
                        st.markdown(" ".join(f'<span class="pill">{s}</span>' for s in job_skills), unsafe_allow_html=True)
                with right:
                    st.write("")
                    already_applied = False
                    try:
                        # The helper performs a parameterized check against application.
                        from db import application_exists
                        already_applied = application_exists(emp_id, job["job_id"])
                    except Exception:
                        pass
                    if already_applied:
                        st.button("Already applied", key=f"applied_{job['job_id']}", disabled=True, use_container_width=True)
                    elif st.button("Apply now", key=f"apply_{job['job_id']}", type="primary", use_container_width=True):
                        try:
                            apply_for_job(emp_id, job["job_id"])
                            st.success(f"Application submitted for {job.get('description') or 'this job'}.")
                            st.rerun()
                        except Exception as exc:
                            st.error("Application could not be submitted.")
                            st.exception(exc)

    with tab_apps:
        st.subheader("Your application tracker")
        applications = get_applications_for_employee(emp_id)
        if applications:
            total = len(applications)
            shortlisted = sum(1 for row in applications if str(row.get("status", "")).lower() == "shortlisted")
            hired = sum(1 for row in applications if str(row.get("status", "")).lower() == "hired")
            m1, m2, m3 = st.columns(3)
            m1.metric("Applications", total)
            m2.metric("Shortlisted", shortlisted)
            m3.metric("Hired", hired)
            df_or_empty(applications)
            st.markdown("#### Manage an application")
            for application in applications:
                app_id = application["application_id"]
                with st.container(border=True):
                    a_left, a_right = st.columns([4, 1.5])
                    with a_left:
                        st.write(f"**{application.get('description') or 'Job application'}** · {application.get('company_name') or 'Company'}")
                        st.caption(f"Application #{app_id} · {application.get('date_of_submission') or 'Submission date unavailable'} · Status: {application.get('status') or 'Not set'}")
                    with a_right:
                        with st.form(f"withdraw_application_{app_id}"):
                            confirm_withdraw = st.checkbox("Confirm withdrawal")
                            withdraw = st.form_submit_button("Withdraw application")
                        if withdraw:
                            if not confirm_withdraw:
                                st.warning("Tick the confirmation box before withdrawing.")
                            else:
                                try:
                                    deleted = withdraw_application(app_id, emp_id)
                                    if deleted:
                                        st.success(f"Application #{app_id} withdrawn.")
                                        st.rerun()
                                    else:
                                        st.warning("No application row was deleted. Refresh and try again.")
                                except Exception as exc:
                                    st.error("This application cannot be deleted, possibly because an interview or offer references it.")
                                    st.exception(exc)
            application_ids = [row["application_id"] for row in applications]
            with st.expander("Interview schedule"):
                interviews = get_interviews_for_application_ids(application_ids)
                df_or_empty(interviews, "No interviews have been scheduled for your applications yet.")
            with st.expander("Job offers"):
                offers = get_employee_offers(emp_id)
                df_or_empty(offers, "No job offers are linked to your profile yet.")
        else:
            st.info("You have not applied to any jobs yet. Explore available opportunities to get started.")

    with tab_profile:
        st.subheader("Your applicant profile")
        profile = get_employee_profile(emp_id) or {}
        name_col = pick_column("employee", "name", required=True)
        resume_col = pick_column("employee", "resume")
        experience_col = pick_column("employee", "experience")
        with st.form("edit_applicant_profile"):
            edited_name = st.text_input("Full name", value=str(profile.get(name_col) or ""))
            edited_experience = st.text_area("Experience", value=str(profile.get(experience_col) or "") if experience_col else "", disabled=not experience_col)
            edited_resume = st.text_area("Resume / profile summary", value=str(profile.get(resume_col) or "") if resume_col else "", disabled=not resume_col)
            save_profile = st.form_submit_button("Save profile", type="primary")
        if save_profile:
            try:
                update_employee_profile(emp_id, edited_name, edited_resume, edited_experience)
                st.success("Profile updated.")
                st.rerun()
            except Exception as exc:
                st.error("Profile update failed.")
                st.exception(exc)

        st.markdown("#### Skills")
        current_skills = get_employee_skills(emp_id) if table_exists("employee_skills") else []
        if current_skills:
            st.write(" · ".join(row["skill_name"] for row in current_skills))
        else:
            st.caption("No skills have been added to this profile yet.")
        available_skills = get_skills() if table_exists("skills") else []
        current_ids = {row["skill_id"] for row in current_skills}
        remaining = [row for row in available_skills if row["skill_id"] not in current_ids]
        if remaining and table_exists("employee_skills"):
            with st.form("add_employee_skill_form"):
                skill = st.selectbox("Add a skill", remaining, format_func=lambda row: row["skill_name"])
                add_skill = st.form_submit_button("Add skill")
            if add_skill:
                try:
                    add_employee_skill(emp_id, skill["skill_id"])
                    st.success("Skill added to your profile.")
                    st.rerun()
                except Exception as exc:
                    st.error("Could not add skill.")
                    st.exception(exc)

        st.markdown("#### Contact numbers")
        phones = get_employee_phones(emp_id)
        if phones:
            for item in phones:
                st.write(f"☎ {item['phone_no']}")
        if table_exists("employee_phone"):
            with st.form("add_phone_form"):
                phone = st.text_input("Add phone number")
                add_phone = st.form_submit_button("Save phone number")
            if add_phone:
                if not phone.strip():
                    st.warning("Enter a phone number first.")
                else:
                    try:
                        add_employee_phone(emp_id, phone)
                        st.success("Phone number saved.")
                        st.rerun()
                    except Exception as exc:
                        st.error("Could not save phone number.")
                        st.exception(exc)


def hr_workspace():
    hero("HR workspace", "Publish roles, review applicants, move candidates through the pipeline, and manage interviews and offers.")
    if not table_exists("hr_manager") or not table_exists("job"):
        st.error("The database must contain hr_manager and job tables to use the HR workspace.")
        return
    managers = get_hr_managers()
    if not managers:
        st.warning("No HR manager records were found. Add an HR manager and link it to a company in pgAdmin first.")
        return

    st.sidebar.markdown("### HR account")
    manager = st.sidebar.selectbox(
        "Continue as",
        managers,
        format_func=lambda row: f"{row['manager_name']} · {row.get('company_name') or 'Company'} · ID {row['employer_id']}",
        key="hr_identity",
    )
    manager_id = manager["employer_id"]
    st.caption(f"HR manager: **{manager['manager_name']}** · {manager.get('company_name') or 'Company not linked'}")

    hr_jobs = get_job_listings(manager_id=manager_id)
    hr_apps = get_applications_for_manager(manager_id) if table_exists("application") else []
    pending_count = sum(1 for row in hr_apps if str(row.get("status", "")).lower() in {"applied", "pending", "under review"})
    short_count = sum(1 for row in hr_apps if str(row.get("status", "")).lower() == "shortlisted")
    hired_count = sum(1 for row in hr_apps if str(row.get("status", "")).lower() == "hired")

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Your job posts", len(hr_jobs))
    k2.metric("Applications", len(hr_apps))
    k3.metric("Awaiting review", pending_count)
    k4.metric("Hired", hired_count)

    tab_jobs, tab_candidates, tab_interviews = st.tabs(["Manage jobs", "Applicants", "Interviews & offers"])

    with tab_jobs:
        st.subheader("Create a job opening")
        with st.form("create_job_form"):
            description = st.text_area("Job title / description *", placeholder="Backend Python Developer — responsibilities, qualifications and role details")
            j1, j2, j3 = st.columns(3)
            with j1:
                salary = st.number_input("Expected salary", min_value=0.0, step=1000.0, value=0.0)
            with j2:
                location = st.text_input("Location *", placeholder="Ahmedabad / Indore / Remote")
            with j3:
                work_type = st.selectbox("Work type", ["On-site", "Hybrid", "Remote"])
            job_skill_options = get_skills() if table_exists("skills") and table_exists("job_skills") else []
            selected_job_skills = st.multiselect(
                "Required skills",
                job_skill_options,
                format_func=lambda row: row["skill_name"],
                help="These requirements appear on the applicant job cards and power skill-based search.",
            ) if job_skill_options else []
            post_job = st.form_submit_button("Publish job", type="primary")
        if post_job:
            if not description.strip() or not location.strip():
                st.error("Job description and location are required.")
            else:
                try:
                    created_job = add_job_for_manager(manager_id, description, int(salary), location, work_type)
                    job_id_column = pick_column("job", "job_id", required=True)
                    for required_skill in selected_job_skills:
                        add_job_skill(created_job[job_id_column], required_skill["skill_id"])
                    st.success("Job published to the applicant portal.")
                    st.rerun()
                except Exception as exc:
                    st.error("Could not publish this job. Check required fields and foreign-key constraints.")
                    st.exception(exc)

        st.markdown("#### Your published jobs")
        df_or_empty(hr_jobs, "You have not published any jobs yet.")

    with tab_candidates:
        st.subheader("Application review pipeline")
        if not hr_apps:
            st.info("No applications have been received for your jobs yet.")
        else:
            statuses = ["Applied", "Under Review", "Shortlisted", "Rejected", "Hired"]
            for application in hr_apps:
                app_id = application["application_id"]
                with st.container(border=True):
                    top_l, top_r = st.columns([4, 1.3])
                    with top_l:
                        st.markdown(f"**{application['employee_name']}**  ·  {application.get('description') or 'Job application'}")
                        st.caption(f"Applicant ID: {application['emp_id']} · Job ID: {application['job_id']} · Application ID: {app_id} · Submitted: {application.get('date_of_submission') or '—'}")
                    with top_r:
                        status_label(application.get("status"))
                    profile = get_employee_profile(application["emp_id"]) or {}
                    with st.expander("View candidate profile"):
                        st.write("**Experience**")
                        experience_col = pick_column("employee", "experience")
                        st.write(profile.get(experience_col) if experience_col else "Not available")
                        resume_col = pick_column("employee", "resume")
                        st.write("**Resume / profile summary**")
                        st.write(profile.get(resume_col) if resume_col else "Not available")
                        skills = get_employee_skills(application["emp_id"])
                        st.write("**Skills:**", ", ".join(x["skill_name"] for x in skills) if skills else "Not listed")
                    current = str(application.get("status") or "Applied")
                    options = list(statuses)
                    if current not in options:
                        options.insert(0, current)
                    with st.form(f"status_form_{app_id}"):
                        new_status = st.selectbox("Update application status", options, index=options.index(current))
                        save_status = st.form_submit_button("Save status")
                    if save_status:
                        try:
                            update_application_status(app_id, new_status)
                            st.success(f"Application {app_id} updated to {new_status}.")
                            st.rerun()
                        except Exception as exc:
                            st.error("Status update failed. The database may enforce a different set of status values.")
                            st.exception(exc)

    with tab_interviews:
        st.subheader("Schedule an interview")
        if not hr_apps:
            st.info("You need an application for one of your jobs before scheduling an interview.")
        elif not table_exists("interview"):
            st.warning("The interview table was not found in the public schema.")
        else:
            options = hr_apps
            with st.form("schedule_interview_form"):
                app_choice = st.selectbox(
                    "Application",
                    options,
                    format_func=lambda row: f"#{row['application_id']} · {row['employee_name']} · {row['description']}",
                )
                d1, d2 = st.columns(2)
                with d1:
                    interview_date = st.date_input("Interview date", value=date.today())
                with d2:
                    interview_time = st.time_input("Interview time", value=time(10, 0))
                feedback = st.text_area("Initial HR feedback (optional)")
                schedule = st.form_submit_button("Schedule interview", type="primary")
            if schedule:
                try:
                    scheduled_at = datetime.combine(interview_date, interview_time)
                    create_interview(app_choice["application_id"], scheduled_at, manager_id, feedback)
                    st.success("Interview scheduled successfully.")
                    st.rerun()
                except Exception as exc:
                    st.error("Interview could not be scheduled. Check the interview table's required columns.")
                    st.exception(exc)

        st.divider()
        st.subheader("Create a job offer")
        if not table_exists("job_offer"):
            st.warning("The job_offer table was not found in the public schema.")
        elif not hr_apps:
            st.info("No applications are available for an offer yet.")
        else:
            with st.form("create_offer_form"):
                offer_app = st.selectbox(
                    "Applicant / application",
                    hr_apps,
                    format_func=lambda row: f"#{row['application_id']} · {row['employee_name']} · {row['description']}",
                    key="offer_application_select",
                )
                offer_status = st.selectbox("Offer status", ["Pending", "Accepted", "Withdrawn"])
                letter = st.text_area("Offer letter / details", placeholder="Role, compensation, start date and any conditions…")
                create_offer = st.form_submit_button("Create job offer", type="primary")
            if create_offer:
                try:
                    create_job_offer(offer_app["application_id"], manager_id, offer_status, letter)
                    st.success("Job offer created.")
                    st.rerun()
                except Exception as exc:
                    st.error("Offer could not be created. The table may require an interview or a different status value.")
                    st.exception(exc)


def insights_workspace():
    hero("Hiring insights", "Run the SQL reports from your Lab 10A query sheet against the live PostgreSQL database.")
    reports = get_reports()
    report_names = list(reports.keys())
    selected_report = st.selectbox("Choose a report", report_names)
    query, params = reports[selected_report]
    st.markdown(f"**{selected_report}**")
    st.caption("The report runs on your live project database. User input values are sent as SQL parameters.")
    extra_params = ()
    if selected_report == "Skills of hired applicants for a selected job":
        jobs = get_job_listings()
        if not jobs:
            st.warning("There are no jobs to select yet.")
            return
        selected_job = st.selectbox(
            "Choose job",
            jobs,
            format_func=lambda row: f"Job {row['job_id']} · {row.get('description') or 'Position'}",
        )
        extra_params = (selected_job["job_id"],)
    with st.expander("View SQL used for this report"):
        st.code(query, language="sql")
        if params or extra_params:
            st.caption(f"Bound parameters: {params + extra_params}")
    if st.button("Run report", type="primary"):
        try:
            from db import fetch_all
            rows = fetch_all(query, tuple(params) + tuple(extra_params))
            st.success(f"Report completed · {len(rows)} row(s)")
            df_or_empty(rows, "The query ran successfully but returned no rows.")
            if rows:
                st.download_button(
                    "Download report as CSV",
                    data=pd.DataFrame(rows).to_csv(index=False).encode("utf-8"),
                    file_name="careerconnect_report.csv",
                    mime="text/csv",
                )
        except Exception as exc:
            st.error("This report could not be run against the current schema. Check the column names or constraints noted below.")
            st.exception(exc)


# ---------------------------------------------------------------------------
# App navigation / entry point
# ---------------------------------------------------------------------------

st.sidebar.title("💼 CareerConnect")
st.sidebar.caption("Job application portal")
show_connection()

workspace = st.sidebar.radio(
    "Choose workspace",
    ["Applicant portal", "HR workspace", "Hiring insights"],
    index=0,
)
st.sidebar.divider()
st.sidebar.caption("Database: `project` · PostgreSQL · public schema")
st.sidebar.caption("Demo build: the current ERD has no password/login table, so the identity selector is for lab demonstration only—not secure authentication.")

if workspace == "Applicant portal":
    applicant_workspace()
elif workspace == "HR workspace":
    hr_workspace()
else:
    insights_workspace()
